"""Stage 2 commands that make and show history: commit, log."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pygit import revparse, worktree
from pygit.cli import command, err, out, parser
from pygit.errors import GitError
from pygit.history import CommitCache, merge_bases, walk
from pygit.ident import ident
from pygit.index import Index
from pygit.objects import Commit
from pygit.pretty import (cleanup_message, date_normal, format_placeholders, full, medium, short,
                          split_message)
from pygit.refs import Refs
from pygit.status import compute
from pygit.treediff import diff_maps, line_counts, shortstat, summary_lines, tree_entries


def _work_repo():
    from pygit.repo import find_repo
    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    return repo


def commit_summary(repo, oid: str, c: Commit, branch_label: bytes, root: bool,
                   date_interesting: bool) -> bytes:
    """`[main (root-commit) abc1234] subject` plus the diffstat summary."""
    abbrev = revparse.short_id(repo, oid)
    subj, _ = split_message(c.message)
    head = b"[" + branch_label + (b" (root-commit)" if root else b"") + b" " + abbrev.encode() + b"] " + subj
    lines = [head + b"\n"]
    a_id = c.author.name + b" <" + c.author.email + b">"
    c_id = c.committer.name + b" <" + c.committer.email + b">"
    if a_id != c_id:
        lines.append(b" Author: " + a_id + b"\n")
    if date_interesting:
        lines.append(b" Date: " + date_normal(c.author) + b"\n")
    old_tree = None
    if c.parents:
        old_tree = Commit.parse(repo.odb.read(c.parents[0])[1]).tree
    pairs = diff_maps(repo.odb, tree_entries(repo.odb, old_tree), tree_entries(repo.odb, c.tree))
    ins = dels = 0
    for p in pairs:
        i, d, _ = line_counts(repo.odb, p)
        ins += i
        dels += d
    # A merge commit gets no diffstat (log shows no diff for merges).
    if pairs and len(c.parents) < 2:
        lines.append(shortstat(len(pairs), ins, dels))
        lines.append(summary_lines(pairs))
    return b"".join(lines)


@command("commit")
def cmd_commit(args):
    p = parser("commit")
    p.add_argument("-m", "--message", dest="messages", action="append", default=[])
    p.add_argument("-F", "--file", dest="file")
    p.add_argument("-a", "--all", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--allow-empty-message", action="store_true")
    p.add_argument("--amend", action="store_true")
    p.add_argument("--no-edit", action="store_true")
    p.add_argument("--cleanup", default=None)
    p.add_argument("--author")
    p.add_argument("--date")
    p.add_argument("-C", "--reuse-message", dest="reuse")
    p.add_argument("--no-verify", "-n", action="store_true")
    p.add_argument("paths", nargs="*")
    a = p.parse_args(args)
    repo = _work_repo()
    if a.paths:
        raise GitError("pygit commit does not support pathspecs yet; use add/rm first")
    refs = Refs(repo)
    head, head_ref = refs.resolve("HEAD")
    branch_target = refs.head_branch()
    idx = Index.read(repo)

    if a.all:
        for e in idx.sorted_entries():
            if e.stage:
                continue
            st = worktree.lstat(repo, e.path)
            if st is None:
                idx.remove(e.path)
            elif e.mode != 0o160000 and worktree.is_modified(repo, e, st):
                idx.add(worktree.entry_for_file(repo, e.path, e))
    if idx.has_conflicts():
        err("error: Committing is not possible because you have unmerged files.\n"
            "hint: Fix them up in the work tree, and then use 'git add/rm <file>'\n"
            "hint: as appropriate to mark resolution and make a commit.\n"
            "fatal: Exiting because of an unresolved conflict.\n")
        return 128

    merge_heads = []
    mh = repo.gitdir / "MERGE_HEAD"
    if mh.is_file():
        merge_heads = [l.strip() for l in mh.read_text().splitlines() if l.strip()]

    old = None
    if a.amend:
        if head is None:
            raise GitError("You have nothing to amend.")
        old = Commit.parse(repo.odb.read(head)[1])
        parents = list(old.parents)
    else:
        parents = ([head] if head else []) + merge_heads

    # The message.
    if a.messages:
        raw = b"\n\n".join(m.encode() for m in a.messages)
    elif a.file:
        raw = sys.stdin.buffer.read() if a.file == "-" else Path(a.file).read_bytes()
    elif a.reuse:
        raw = Commit.parse(repo.odb.read(revparse.resolve(repo, a.reuse))[1]).message
    elif a.amend:
        raw = old.message
    elif merge_heads and (repo.gitdir / "MERGE_MSG").is_file():
        raw = (repo.gitdir / "MERGE_MSG").read_bytes()
    else:
        raise GitError("pygit commit needs a message (-m or -F); it does not open an editor")
    # git's "default" cleanup is "strip" only when an editor runs; pygit never
    # opens one, so it is "whitespace" (a merge's "# Conflicts:" lines stay,
    # as with `git commit --no-edit`).
    mode = a.cleanup or "whitespace"
    if mode == "default":
        mode = "whitespace"
    msg = cleanup_message(raw, mode)
    if a.amend and not (a.messages or a.file):
        msg = old.message  # --no-edit keeps the message verbatim

    tree = idx.write_tree(repo.odb)
    # "Nothing to commit": compare with the commit this one will sit on top
    # of (HEAD, or HEAD's first parent when amending a non-merge).
    if not merge_heads and not a.allow_empty and not (a.amend and len(parents) > 1):
        base = parents[0] if parents else None
        base_tree = Commit.parse(repo.odb.read(base)[1]).tree if base else None
        if base_tree == tree or (base is None and not idx.entries):
            from pygit.commands.porcelain import long_status
            from pygit.pathspec import cwd_prefix
            from pygit.status import staged_changes
            st = compute(repo)
            if a.amend:
                ref = tree_entries(repo.odb, base_tree)
                st.staged = staged_changes(ref, idx, True, repo.odb)
            out(long_status(repo, st, cwd_prefix(repo), from_commit=True, amend=a.amend))
            if a.amend:
                err("You asked to amend the most recent commit, but doing so would make\n"
                    "it empty. You can repeat your command with --allow-empty, or you can\n"
                    'remove the commit entirely with "git reset HEAD^".\n')
            return 1
    if not msg and not a.allow_empty_message:
        err("Aborting commit due to empty commit message.\n")
        return 1

    author = old.author if a.amend and not a.author else ident(repo, "author")
    if a.author:
        name, _, rest = a.author.partition("<")
        if not rest:
            raise GitError(f"--author '{a.author}' is not 'Name <email>' and matches no existing author")
        author = type(author)(name.strip().encode(), rest.rstrip(">").strip().encode(),
                              author.timestamp, author.tz)
    if a.date:
        from pygit.ident import parse_date
        ts, tz = parse_date(a.date)
        author = type(author)(author.name, author.email, ts, tz)
    committer = ident(repo, "committer")
    c = Commit(tree, parents, author, committer, msg, list(old.extra) if a.amend else [])
    if a.amend:
        c.extra = [(k, v) for k, v in c.extra if k not in (b"gpgsig", b"gpgsig-sha256")]
    oid = repo.odb.write(b"commit", c.serialize())
    subj, _ = split_message(msg)
    if a.amend:
        reflog = "commit (amend): "
    elif head is None:
        reflog = "commit (initial): "
    elif merge_heads:
        reflog = "commit (merge): "
    else:
        reflog = "commit: "
    refs.update("HEAD", oid, reflog + subj.decode("utf-8", "replace"))
    idx.write()
    for name in ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE", "SQUASH_MSG"):
        try:
            (repo.gitdir / name).unlink()
        except FileNotFoundError:
            pass
    if not a.quiet:
        if branch_target and branch_target.startswith("refs/heads/"):
            label = branch_target[len("refs/heads/"):].encode()
        else:
            label = b"detached HEAD"
        out(commit_summary(repo, oid, c, label, root=not parents,
                           date_interesting=bool(a.amend or a.date or a.reuse)))
    return 0


# -- log -------------------------------------------------------------------------------

def parse_revisions(repo, specs: list[str]) -> tuple[list[str], list[str]]:
    """Positive and negative commit ids from revision arguments."""
    include, exclude = [], []

    def commit_of(spec):
        return revparse.peel(repo, revparse.resolve(repo, spec), b"commit")

    for spec in specs:
        if "..." in spec:
            left, right = spec.split("...", 1)
            l, r = commit_of(left or "HEAD"), commit_of(right or "HEAD")
            include += [l, r]
            exclude += merge_bases(repo, l, r)
        elif ".." in spec:
            left, right = spec.split("..", 1)
            exclude.append(commit_of(left or "HEAD"))
            include.append(commit_of(right or "HEAD"))
        elif spec.startswith("^"):
            exclude.append(commit_of(spec[1:]))
        else:
            include.append(commit_of(spec))
    return include, exclude


@command("log")
def cmd_log(args):
    from pygit.repo import find_repo
    repo = find_repo()
    max_count = None
    fmt = "medium"
    user_format = None
    tformat = True
    reverse = first_parent = False
    abbrev_commit = False
    diff_formats = []
    renames = repo.config.get_bool("diff.renames", True)
    revs = []
    paths = []
    it = iter(args)
    for arg in it:
        if arg == "--":
            paths = list(it)
            break
        if arg.startswith("-n") and arg[2:].isdigit():
            max_count = int(arg[2:])
        elif arg == "-n":
            max_count = int(next(it))
        elif arg.startswith("--max-count="):
            max_count = int(arg.split("=", 1)[1])
        elif arg.startswith("-") and arg[1:].isdigit():
            max_count = int(arg[1:])
        elif arg == "--oneline":
            fmt = "oneline"
            abbrev_commit = True
        elif arg.startswith("--pretty=") or arg.startswith("--format="):
            val = arg.split("=", 1)[1]
            if arg.startswith("--format="):
                user_format, tformat = val, True
                fmt = "user"
            elif val.startswith("format:"):
                user_format, tformat = val[len("format:"):], False
                fmt = "user"
            elif val.startswith("tformat:"):
                user_format, tformat = val[len("tformat:"):], True
                fmt = "user"
            elif val in ("oneline", "short", "medium", "full", "fuller", "raw"):
                fmt = val
            else:
                user_format, tformat = val, True
                fmt = "user"
        elif arg == "--pretty":
            fmt = "medium"
        elif arg == "--reverse":
            reverse = True
        elif arg == "--first-parent":
            first_parent = True
        elif arg == "--abbrev-commit":
            abbrev_commit = True
        elif arg == "--no-abbrev-commit":
            abbrev_commit = False
        elif arg in DIFF_FORMAT_FLAGS:
            diff_formats.append(DIFF_FORMAT_FLAGS[arg])
        elif arg in ("-s", "--no-patch"):
            diff_formats.clear()
        elif arg == "--no-renames":
            renames = False
        elif arg in ("--no-decorate", "--no-color", "--decorate=no"):
            pass
        elif arg.startswith("-"):
            raise GitError(f"unrecognized argument: {arg}")
        elif paths:
            paths.append(arg)  # after the first path, everything is a path
        else:
            revs.append(arg)
    # Without "--", an argument that is not a revision but names a file is
    # a path (and so is everything after it).
    if revs and not paths:
        for i, r in enumerate(revs):
            spec = r[1:] if r.startswith("^") else r
            try:
                # A range is checked by resolving its ends ("../x" is a path);
                # ".." alone, with no ends, is a path too.
                if spec.strip(".") == "":
                    raise GitError("not a revision")
                for end in (spec.replace("...", "..").split("..") if ".." in spec else [spec]):
                    revparse.resolve(repo, end or "HEAD")
            except GitError:
                if os.path.exists(r):
                    revs, paths = revs[:i], revs[i:]
                break
    if not revs:
        head, name = Refs(repo).resolve("HEAD")
        if head is None:
            branch = Refs(repo).head_branch() or "HEAD"
            short_name = branch[len("refs/heads/"):] if branch.startswith("refs/heads/") else branch
            raise GitError(f"your current branch '{short_name}' does not have any commits yet")
        revs = ["HEAD"]
    try:
        include, exclude = parse_revisions(repo, revs)
    except GitError:
        bad = next(r for r in revs)
        raise GitError(f"ambiguous argument '{bad}': unknown revision or path not in the working tree.\n"
                       "Use '--' to separate paths from revisions, like this:\n"
                       "'git <command> [<revision>...] -- [<file>...]'")
    cache = CommitCache(repo)
    oids = []
    commits = walk_paths(repo, include, exclude, paths, first_parent, cache) if paths \
        else walk(repo, include, exclude, first_parent, cache)
    for oid in commits:
        oids.append(oid)
        if max_count is not None and len(oids) >= max_count and not reverse:
            break
    if max_count is not None:
        oids = oids[:max_count]
    if reverse:
        oids.reverse()
    view = LogView(repo, cache, fmt, user_format, tformat, abbrev_commit, diff_formats, renames)
    out(view.render(oids))
    return 0


DIFF_FORMAT_FLAGS = {"-p": "patch", "-u": "patch", "--patch": "patch", "--stat": "stat",
                     "--numstat": "numstat", "--shortstat": "shortstat", "--name-only": "name-only",
                     "--name-status": "name-status", "--summary": "summary"}


class LogView:
    """Commit entries as `git log` prints them, optionally with diffs
    against the first parent (merges get none, as in `git log -p`)."""

    def __init__(self, repo, cache, fmt, user_format, tformat, abbrev_commit, diff_formats, renames=True):
        self.repo, self.cache = repo, cache
        self.fmt, self.user_format, self.tformat = fmt, user_format, tformat
        self.abbrev_commit = abbrev_commit
        self.diff_formats = diff_formats
        self.renames = renames
        self._abbrevs = {}

    def abbrev(self, o):
        if o not in self._abbrevs:
            self._abbrevs[o] = revparse.short_id(self.repo, o)
        return self._abbrevs[o]

    def header(self, oid) -> bytes:
        c = self.cache.get(oid)
        shown = self.abbrev(oid) if self.abbrev_commit else oid
        fmt = self.fmt
        if fmt == "oneline":
            subj, _ = split_message(c.message)
            return shown.encode() + b" " + subj + b"\n"
        if fmt == "user":
            return format_placeholders(self.user_format.encode(), oid, c, self.abbrev)
        if fmt == "short":
            return short(self.repo, shown, c, self.abbrev)
        if fmt in ("full", "fuller"):
            return full(self.repo, shown, c, self.abbrev, fuller=(fmt == "fuller"))
        if fmt == "raw":
            raw = self.repo.odb.read(oid)[1]
            headers, _, msg = raw.partition(b"\n\n")
            from pygit.pretty import indented_message
            return (b"commit " + oid.encode() + b"\n" + headers + b"\n\n" +
                    indented_message(msg, expand_tabs=False))
        return medium(self.repo, shown, c, self.abbrev)

    def diff(self, oid):
        """The diff text, b"" if files changed but nothing is printed
        (e.g. --summary of a plain edit), or None if nothing changed."""
        if not self.diff_formats:
            return None
        c = self.cache.get(oid)
        formats = self.diff_formats
        if len(c.parents) > 1:
            if not self.combined:
                return None
            # Combined mode: the stat-like parts are against the first
            # parent; the patch is the dense combined diff of the paths
            # that differ from every parent.
            patch = self.combined_patch(c) if "patch" in formats else b""
            formats = [f for f in formats if f not in ("patch", "name-only", "name-status")]
            if not formats:
                return patch or None
            old_tree = self.cache.get(c.parents[0]).tree
            stat = render_diff(self.repo, tree_entries(self.repo.odb, old_tree),
                               tree_entries(self.repo.odb, c.tree), formats, self.renames) or b""
            return stat + (b"\n" if stat and patch else b"") + patch
        old_tree = self.cache.get(c.parents[0]).tree if c.parents else None
        return render_diff(self.repo, tree_entries(self.repo.odb, old_tree),
                           tree_entries(self.repo.odb, c.tree), formats, self.renames)

    combined = False  # show: merges get a (dense) combined diff

    def combined_patch(self, c) -> bytes:
        """diff_tree_combined: `diff --cc` for paths differing from all parents."""
        from pygit.combined import combined_patch
        odb = self.repo.odb
        result = tree_entries(odb, c.tree)
        parent_maps = [tree_entries(odb, self.cache.get(p).tree) for p in c.parents]
        paths = set(result)
        for m in parent_maps:
            paths |= set(m)
        out_parts = []
        for path in sorted(paths):
            r = result.get(path)
            if all(m.get(path) != r for m in parent_maps):
                parents = [m.get(path) or (0, None) for m in parent_maps]
                pdata = [odb.read(o)[1] if o else b"" for _, o in parents]
                rmode, roid = r if r else (0, None)
                rdata = odb.read(roid)[1] if roid else b""
                out_parts.append(combined_patch(self.repo, path, roid, rmode, rdata, parents, pdata,
                                                working_tree=False))
        return b"".join(out_parts)

    def entry(self, oid) -> bytes:
        head = self.header(oid)
        if self.fmt == "user" and self.tformat:
            head += b"\n"
        d = self.diff(oid)
        merge = len(self.cache.get(oid).parents) > 1
        if d is not None or (merge and self.combined and self.diff_formats):
            d = d or b""
            # A combined diff of a clean merge is empty, but git still
            # prints the separator. With both --stat and a patch the
            # separator is "---" (log_tree_diff_flush).
            if merge:
                sep = b"\n"  # combined output always uses a blank line
            elif self.fmt == "oneline":
                sep = b""
            elif "stat" in self.diff_formats and "patch" in self.diff_formats:
                sep = b"---\n"
            else:
                sep = b"\n"
            head += sep + d
        return head

    def render(self, oids) -> bytes:
        entries = [self.entry(o) for o in oids]
        if self.fmt == "oneline" or (self.fmt == "user" and self.tformat):
            return b"".join(entries)
        return b"\n".join(entries)


@command("show")
def cmd_show(args):
    """Commits (with their patch), annotated tags, trees and blobs."""
    from pygit.objects import Tag, parse_tree
    from pygit.repo import find_repo
    repo = find_repo()
    fmt, user_format, tformat = "medium", None, True
    abbrev_commit = False
    diff_formats = ["patch"]
    explicit_diff = False
    names = []
    for arg in args:
        if arg in DIFF_FORMAT_FLAGS:
            if not explicit_diff:
                diff_formats = []
                explicit_diff = True
            diff_formats.append(DIFF_FORMAT_FLAGS[arg])
        elif arg in ("-s", "--no-patch"):
            diff_formats, explicit_diff = [], True
        elif arg == "--oneline":
            fmt, abbrev_commit = "oneline", True
        elif arg.startswith("--format=") or arg.startswith("--pretty="):
            val = arg.split("=", 1)[1]
            if val in ("oneline", "short", "medium", "full", "fuller", "raw"):
                fmt = val
            else:
                fmt = "user"
                tformat = not val.startswith("format:")
                user_format = val.split(":", 1)[1] if val.startswith(("format:", "tformat:")) else val
        elif arg.startswith("-"):
            raise GitError(f"unrecognized argument: {arg}")
        else:
            names.append(arg)
    if not names:
        names = ["HEAD"]
    cache = CommitCache(repo)
    view = LogView(repo, cache, fmt, user_format, tformat, abbrev_commit, diff_formats)
    view.combined = True
    pieces = []
    for name in names:
        try:
            oid = revparse.resolve(repo, name)
        except GitError:
            raise GitError(f"ambiguous argument '{name}': unknown revision or path not in the working tree.\n"
                           "Use '--' to separate paths from revisions, like this:\n"
                           "'git <command> [<revision>...] -- [<file>...]'")
        while True:
            t, data = repo.odb.read(oid)
            if t == b"tag":
                tag = Tag.parse(data)
                text = b"tag " + tag.tag + b"\n"
                if tag.tagger is not None:
                    text += b"Tagger: " + tag.tagger.name + b" <" + tag.tagger.email + b">\n"
                    text += b"Date:   " + date_normal(tag.tagger) + b"\n"
                text += b"\n" + tag.message
                pieces.append(text if text.endswith(b"\n") else text + b"\n")
                oid = tag.object
                continue
            if t == b"commit":
                pieces.append(view.entry(oid))
            elif t == b"tree":
                lines = [e.name + (b"/" if e.is_tree else b"") + b"\n" for e in parse_tree(data)]
                pieces.append(b"tree " + name.encode() + b"\n\n" + b"".join(lines))
            else:
                pieces.append(data)
            break
    # Commit and tag entries are separated by a blank line, as in log.
    outp = b""
    for i, piece in enumerate(pieces):
        if i and (fmt not in ("oneline",) and not (fmt == "user" and tformat)):
            outp += b"\n"
        outp += piece
    out(outp)
    return 0


def render_diff(repo, old: dict, new: dict, formats: list[str], renames: bool = True) -> bytes:
    """The diff part of a log entry, in git's order: stats before the patch."""
    from pygit import diffout
    from pygit.treediff import diff_maps, summary_lines
    pairs = diff_maps(repo.odb, old, new, renames=renames)
    if not pairs:
        return None  # no changes: no separator either
    contents = diffout.Contents(repo)
    parts = []
    if "stat" in formats:
        parts.append(diffout.stat(pairs, contents))
    if "shortstat" in formats and "stat" not in formats:
        parts.append(diffout.shortstat_for(pairs, contents))
    if "numstat" in formats:
        parts.insert(0, diffout.numstat(pairs, contents))
    if "summary" in formats:
        parts.append(summary_lines(pairs))
    if "name-only" in formats:
        parts.append(diffout.name_only(pairs))
    if "name-status" in formats:
        parts.append(diffout.name_status(pairs))
    text = b"".join(parts)
    if "patch" in formats:
        patch = diffout.patch(repo, pairs, contents)
        text = text + (b"\n" if text else b"") + patch
    return text


def walk_paths(repo, include, exclude, paths, first_parent, cache):
    """Path-limited history with git's default simplification.

    A commit is shown when it is not TREESAME to its parents for `paths`.
    A merge that is TREESAME to one of its parents is hidden, and only that
    parent is followed (the history that brought the paths in unchanged).
    """
    import heapq
    from pygit.history import ancestors
    from pygit.pathspec import Pathspec
    spec = Pathspec(repo, paths)
    trees = {}

    def limited(tree_oid):
        if tree_oid not in trees:
            trees[tree_oid] = {p: v for p, v in tree_entries(repo.odb, tree_oid).items() if spec.matches(p)}
        return trees[tree_oid]

    hidden = ancestors(repo, list(exclude), cache) if exclude else set()
    seen, heap, counter = set(), [], 0

    def push(o):
        nonlocal counter
        if o in seen or o in hidden:
            return
        seen.add(o)
        heapq.heappush(heap, (-cache.get(o).committer.timestamp, counter, o))
        counter += 1

    for o in include:
        push(o)
    while heap:
        _, _, oid = heapq.heappop(heap)
        c = cache.get(oid)
        mine = limited(c.tree)
        parents = c.parents[:1] if first_parent else c.parents
        if not parents:
            if mine:
                yield oid
            continue
        same = [p for p in parents if limited(cache.get(p).tree) == mine]
        if same:
            if len(parents) == 1:
                push(parents[0])
            else:
                push(same[0])
            continue
        yield oid
        for p in parents:
            push(p)

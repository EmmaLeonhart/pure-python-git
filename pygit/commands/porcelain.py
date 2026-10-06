"""Stage 2 commands: the index (ls-files, write-tree, read-tree, update-index),
ignore rules (check-ignore), and add, rm, status."""

from __future__ import annotations

import os
import stat as statmod
import sys

from pygit import revparse, worktree
from pygit.cli import command, err, out, parser
from pygit.errors import GitError
from pygit.ignore import IgnoreRules
from pygit.index import Index, IndexEntry
from pygit.pathspec import Pathspec, cwd_prefix, relative_to_cwd, to_repo_path
from pygit.quote import quote_path
from pygit.repo import find_repo
from pygit.status import compute, head_commit, head_tree_entries


def work_repo():
    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    return repo


# -- index plumbing ------------------------------------------------------------------

@command("ls-files")
def cmd_ls_files(args):
    p = parser("ls-files")
    p.add_argument("-c", "--cached", action="store_true")
    p.add_argument("-s", "--stage", action="store_true")
    p.add_argument("-o", "--others", action="store_true")
    p.add_argument("-i", "--ignored", action="store_true")
    p.add_argument("-d", "--deleted", action="store_true")
    p.add_argument("-m", "--modified", action="store_true")
    p.add_argument("-u", "--unmerged", action="store_true")
    p.add_argument("-z", action="store_true")
    p.add_argument("--exclude-standard", action="store_true")
    p.add_argument("--directory", action="store_true")
    p.add_argument("--full-name", action="store_true")
    p.add_argument("paths", nargs="*")
    a = p.parse_args(args)
    repo = find_repo()
    idx = Index.read(repo)
    prefix = b"" if a.full_name else cwd_prefix(repo)
    spec = Pathspec(repo, a.paths)
    scope = cwd_prefix(repo)
    end = b"\0" if a.z else b"\n"

    def show(path: bytes) -> bytes:
        rel = relative_to_cwd(path, prefix)
        return rel if a.z else quote_path(rel)

    def in_scope(path: bytes) -> bool:
        # Without a pathspec, ls-files is limited to the cwd.
        return spec.matches(path) if spec else path.startswith(scope)

    if a.unmerged:
        a.stage = True
    default = not (a.others or a.deleted or a.modified or a.unmerged or a.cached or a.stage)
    if a.others:
        rules = IgnoreRules(repo) if a.exclude_standard else None
        if rules is None:
            class _NoRules:
                def is_ignored_here(self, *_):
                    return False
            rules = _NoRules()
        u, ig = worktree.untracked(repo, idx, rules, all_files=not a.directory, include_ignored=a.ignored)
        for path in (ig if a.ignored else u):
            if in_scope(path.rstrip(b"/")) or in_scope(path):
                out(show(path) + end)
        return 0
    for e in idx.sorted_entries():
        if not in_scope(e.path):
            continue
        if a.unmerged and not e.stage:
            continue
        if a.cached or default or a.stage:
            if a.stage:
                out(b"%06o %s %d\t" % (e.mode, e.oid.encode(), e.stage) + show(e.path) + end)
            else:
                out(show(e.path) + end)
        if a.deleted and worktree.lstat(repo, e.path) is None:
            out(show(e.path) + end)
        if a.modified and not e.stage and worktree.is_modified(repo, e):
            out(show(e.path) + end)
    return 0


@command("write-tree")
def cmd_write_tree(args):
    p = parser("write-tree")
    p.add_argument("--missing-ok", action="store_true")
    p.parse_args(args)
    repo = find_repo()
    out(Index.read(repo).write_tree(repo.odb).encode() + b"\n")
    return 0


@command("read-tree")
def cmd_read_tree(args):
    p = parser("read-tree")
    p.add_argument("--empty", action="store_true")
    p.add_argument("-u", action="store_true")
    p.add_argument("-m", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("trees", nargs="*")
    a = p.parse_args(args)
    repo = find_repo()
    if a.empty:
        Index(repo.gitdir / "index").write()
        return 0
    if len(a.trees) != 1:
        raise GitError("pygit read-tree supports exactly one tree (or --empty)")
    tree = revparse.peel(repo, revparse.resolve(repo, a.trees[0]), b"tree")
    idx = Index.from_tree(repo, tree)
    if a.u or a.m:
        from pygit.checkout import checkout_index
        old = Index.read(repo)
        # Keep stat data for entries that did not change, so the index stays fresh.
        for key, e in idx.entries.items():
            o = old.entries.get(key)
            if o is not None and o.oid == e.oid and o.mode == e.mode:
                idx.entries[key] = o
        if a.u:
            checkout_index(repo, old, idx)
    idx.write()
    return 0


@command("update-index")
def cmd_update_index(args):
    repo = find_repo()
    idx = Index.read(repo)
    add = remove = force_remove = quiet = False
    i = 0
    rc = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--add":
            add = True
        elif arg == "--remove":
            remove = True
        elif arg == "--force-remove":
            force_remove = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg == "--refresh" or arg == "--really-refresh":
            worktree.refresh(repo, idx)
            for e in idx.sorted_entries():
                if not e.stage and worktree.is_modified(repo, e):
                    if not quiet:
                        out(relative_to_cwd(e.path, cwd_prefix(repo)) + b": needs update\n")
                    rc = 1
        elif arg == "--cacheinfo":
            if "," in args[i]:
                mode, oid, path = args[i].split(",", 2)
                i += 1
            else:
                mode, oid, path = args[i:i + 3]
                i += 3
            idx.add(IndexEntry(path=os.fsencode(path), oid=oid, mode=int(mode, 8)))
        elif arg.startswith("--chmod="):
            flag = arg.split("=", 1)[1]
            path = to_repo_path(repo, args[i])
            i += 1
            e = idx.get(path)
            if e is None:
                raise GitError(f"Unable to mark file {args[i - 1]}")
            e.mode = 0o100755 if flag == "+x" else 0o100644
        elif arg == "--":
            continue
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            path = to_repo_path(repo, arg)
            st = worktree.lstat(repo, path)
            if force_remove or (st is None and remove):
                idx.remove(path)
            elif st is None:
                raise GitError(f"{arg}: does not exist and --remove not passed\n"
                               f"fatal: Unable to process path {arg}")
            elif idx.get(path) is None and not add:
                raise GitError(f"{arg}: cannot add to the index - missing --add option?\n"
                               f"fatal: Unable to process path {arg}")
            else:
                idx.add(worktree.entry_for_file(repo, path, idx.get(path)))
    idx.write()
    return rc


@command("check-ignore")
def cmd_check_ignore(args):
    p = parser("check-ignore")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-n", "--non-matching", action="store_true")
    p.add_argument("--no-index", action="store_true")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("-z", action="store_true")
    p.add_argument("paths", nargs="*")
    a = p.parse_args(args)
    repo = work_repo()
    paths = list(a.paths)
    if a.stdin:
        data = sys.stdin.buffer.read()
        paths += [os.fsdecode(x) for x in data.split(b"\0" if a.z else b"\n") if x]
    if not paths:
        raise GitError("no path specified")
    rules = IgnoreRules(repo)
    idx = None if a.no_index else Index.read(repo)
    found = False
    for arg in paths:
        path = to_repo_path(repo, arg)
        is_dir = arg.endswith("/") or (repo.worktree / os.fsdecode(path)).is_dir()
        pat = None
        if idx is None or idx.get(path) is None:
            parts = path.split(b"/")
            # Excluded parent directories decide first, as git does.
            for i in range(1, len(parts)):
                m = rules.match(b"/".join(parts[:i]), True)
                if m is not None and not m.negated:
                    pat = m
                    break
            if pat is None:
                pat = rules.match(path, is_dir)
        shown = os.fsencode(arg)
        if pat is not None and (not pat.negated or a.verbose):
            # With -v a negated match is reported, and counts for the exit code.
            found = True
            if a.quiet:
                continue
            if a.verbose:
                src = pat.source.encode()
                if a.z:
                    out(src + b"\0%d\0" % pat.lineno + pat.raw + b"\0" + shown + b"\0")
                else:
                    out(src + b":%d:" % pat.lineno + pat.raw + b"\t" + quote_path(shown) + b"\n")
            else:
                out(shown + (b"\0" if a.z else b"\n"))
        elif a.non_matching and a.verbose and not a.quiet:
            out(b"::\t" + quote_path(shown) + b"\n" if not a.z else b"\0\0\0" + shown + b"\0")
    return 0 if found else 1


# -- add / rm -----------------------------------------------------------------------

@command("add")
def cmd_add(args):
    p = parser("add")
    p.add_argument("-A", "--all", action="store_true")
    p.add_argument("-u", "--update", action="store_true")
    p.add_argument("-f", "--force", action="store_true")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--ignore-removal", "--no-all", dest="no_all", action="store_true")
    p.add_argument("paths", nargs="*")
    a = p.parse_args(args)
    repo = work_repo()
    if not a.paths and not (a.all or a.update):
        err("Nothing specified, nothing added.\n"
            "hint: Maybe you wanted to say 'git add .'?\n"
            "hint: Disable this message with \"git config set advice.addEmptyPathspec false\"\n")
        return 0
    idx = Index.read(repo)
    spec = Pathspec(repo, a.paths)
    rules = IgnoreRules(repo)
    matched = [False] * len(spec.items)
    ignored_named = []
    changed = []

    def note(i):
        if spec and i is not None:
            matched[i] = True

    def ignored_dir_of(path: bytes) -> bytes:
        """git names the outermost excluded directory, not the file inside it."""
        parts = path.split(b"/")
        for k in range(1, len(parts)):
            parent = b"/".join(parts[:k])
            if rules.is_ignored_here(parent, True):
                return parent
        return path

    # Tracked files: modifications and (unless --no-all) removals.
    for e in idx.sorted_entries():
        i = spec.matching_item(e.path) if spec else 0
        if spec and i is None:
            continue
        note(i)
        st = worktree.lstat(repo, e.path)
        if st is None or (statmod.S_ISDIR(st.st_mode) and e.mode != 0o160000):
            if not a.no_all:
                changed.append(("remove", e.path))
            continue
        if e.mode == 0o160000:
            continue
        if e.stage or worktree.is_modified(repo, e, st):
            changed.append(("add", e.path))
    # Untracked files.
    if not a.update:
        tracked = idx.paths()
        for path, is_repo in worktree.walk_files(repo, None if a.force else rules):
            if path in tracked:
                continue
            i = spec.matching_item(path) if spec else 0
            if spec and i is None:
                continue
            if not a.force and rules.is_ignored(path, is_repo):
                # Only explicitly named ignored paths are an error.
                if spec and spec.items[i] == path:
                    ignored_named.append(spec.args[i])
                    note(i)
                continue
            note(i)
            if is_repo:
                continue  # nested repositories: not added as gitlinks here
            changed.append(("add", path))
        # A named path that is (or is inside) an ignored directory: walk_files
        # pruned it, so it was never seen above.
        for n, item in enumerate(spec.items):
            fp = repo.worktree / os.fsdecode(item)
            if not matched[n] and not a.force and item and fp.exists() \
                    and rules.is_ignored(item, fp.is_dir()):
                shown = relative_to_cwd(ignored_dir_of(item), cwd_prefix(repo))
                ignored_named.append(os.fsdecode(shown))
                matched[n] = True
    for n, ok in enumerate(matched):
        if not ok and spec.items[n] != b"" and not (repo.worktree / os.fsdecode(spec.items[n])).exists():
            raise GitError(f"pathspec '{spec.args[n]}' did not match any files")
    prefix = cwd_prefix(repo)
    for kind, path in sorted(changed, key=lambda c: c[1]):
        if a.dry_run or a.verbose:
            word = b"add" if kind == "add" else b"remove"
            out(word + b" '" + relative_to_cwd(path, prefix) + b"'\n")
        if a.dry_run:
            continue
        if kind == "add":
            idx.add(worktree.entry_for_file(repo, path, idx.get(path)))
        else:
            idx.remove(path)
    if not a.dry_run:
        idx.write()
    if ignored_named:
        err("The following paths are ignored by one of your .gitignore files:\n" +
            "".join(f"{x}\n" for x in ignored_named) +
            "hint: Use -f if you really want to add them.\n"
            "hint: Disable this message with \"git config set advice.addIgnoredFile false\"\n")
        return 1
    return 0


@command("rm")
def cmd_rm(args):
    p = parser("rm")
    p.add_argument("--cached", action="store_true")
    p.add_argument("-r", action="store_true")
    p.add_argument("-f", "--force", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.add_argument("--ignore-unmatch", action="store_true")
    p.add_argument("paths", nargs="+")
    a = p.parse_args(args)
    repo = work_repo()
    idx = Index.read(repo)
    spec = Pathspec(repo, a.paths)
    targets = []
    for n, item in enumerate(spec.items):
        hits = [e.path for e in idx.sorted_entries()
                if not e.stage and Pathspec._item_matches(item, e.path)]
        hits += [path for path in idx.conflicted_paths() if Pathspec._item_matches(item, path)]
        if not hits:
            if a.ignore_unmatch:
                continue
            raise GitError(f"pathspec '{spec.args[n]}' did not match any files")
        if not a.r and any(h != item for h in hits) and not any(c in item for c in b"*?["):
            raise GitError(f"not removing '{spec.args[n]}' recursively without -r")
        targets += hits
    targets = sorted(set(targets))
    if not a.force:
        head = head_tree_entries(repo)
        local, staged, both = [], [], []
        for path in targets:
            e = idx.get(path)
            if e is None:
                continue
            h = head.get(path)
            staged_diff = h is None or h != (e.mode, e.oid)
            st = worktree.lstat(repo, path)
            wt_diff = st is not None and worktree.is_modified(repo, e, st)
            if a.cached:
                if staged_diff and wt_diff and h is not None:
                    both.append(path)
                elif staged_diff and wt_diff and h is None:
                    both.append(path)
            else:
                if staged_diff and wt_diff:
                    both.append(path)
                elif staged_diff and st is not None:
                    staged.append(path)
                elif wt_diff:
                    local.append(path)
        msgs = []
        prefix = cwd_prefix(repo)

        def block(paths, singular, plural, hint):
            if not paths:
                return
            head_line = singular if len(paths) == 1 else plural
            msgs.append(f"error: {head_line}\n" +
                        "".join(f"    {os.fsdecode(relative_to_cwd(x, prefix))}\n" for x in paths) + hint)

        block(both, "the following file has staged content different from both the\nfile and the HEAD:",
              "the following files have staged content different from both the\nfile and the HEAD:",
              "(use -f to force removal)\n")
        block(staged, "the following file has changes staged in the index:",
              "the following files have changes staged in the index:",
              "(use --cached to keep the file, or -f to force removal)\n")
        block(local, "the following file has local modifications:",
              "the following files have local modifications:",
              "(use --cached to keep the file, or -f to force removal)\n")
        if msgs:
            err("".join(msgs))
            return 1
    prefix = cwd_prefix(repo)
    for path in targets:
        if not a.quiet:
            out(b"rm '" + relative_to_cwd(path, prefix) + b"'\n")
        if a.dry_run:
            continue
        idx.remove(path)
        if not a.cached:
            fp = worktree.fs_path(repo, path)
            try:
                if fp.is_symlink() or fp.is_file():
                    fp.unlink()
            except FileNotFoundError:
                pass
            # Remove directories left empty.
            d = fp.parent
            while d != repo.worktree:
                try:
                    d.rmdir()
                except OSError:
                    break
                d = d.parent
    if not a.dry_run:
        idx.write()
    return 0


# -- status ---------------------------------------------------------------------------

LABELS = {"A": "new file:", "M": "modified:", "D": "deleted:", "R": "renamed:",
          "T": "typechange:", "C": "copied:"}
UNMERGED_LABELS = {"DD": "both deleted:", "AU": "added by us:", "UD": "deleted by them:",
                   "UA": "added by them:", "DU": "deleted by us:", "AA": "both added:",
                   "UU": "both modified:"}


def branch_line(repo) -> bytes:
    from pygit.refs import Refs
    refs = Refs(repo)
    target = refs.head_branch()
    if target is not None:
        name = target[len("refs/heads/"):] if target.startswith("refs/heads/") else target
        return b"On branch " + name.encode() + b"\n"
    from pygit.branching import detached_description
    return detached_description(repo) + b"\n"


def long_status(repo, st, prefix: bytes, hints: bool = True, show_untracked: bool = True,
                from_commit: bool = False, amend: bool = False) -> bytes:
    """The default `git status` output.

    `from_commit`: the status `git commit` prints when there is nothing to
    commit ("Initial commit" instead of "No commits yet"); `amend`: the
    same when amending, which ends with "No changes".
    """
    lines = []
    lines.append(branch_line(repo))
    born = head_commit(repo) is not None
    tracking = None
    try:
        from pygit.branching import tracking_info
        tracking = tracking_info(repo)
    except ImportError:
        pass
    # Every paragraph after the branch line ends with a blank line.
    if tracking:
        lines.append(tracking + b"\n")
    from pygit.merging import merge_state_lines
    lines.extend(merge_state_lines(repo, st))
    if not born:
        lines.append(b"\nInitial commit\n\n" if from_commit else b"\nNo commits yet\n\n")

    def show(path: bytes) -> bytes:
        return quote_path(relative_to_cwd(path, prefix))

    if st.staged:
        lines.append(b"Changes to be committed:\n")
        if hints:
            lines.append(b'  (use "git restore --staged <file>..." to unstage)\n' if born
                         else b'  (use "git rm --cached <file>..." to unstage)\n')
        for c in st.staged:
            label = LABELS[c.status].ljust(12).encode()
            if c.status == "R":
                lines.append(b"\t" + label + show(c.orig) + b" -> " + show(c.path) + b"\n")
            else:
                lines.append(b"\t" + label + show(c.path) + b"\n")
        lines.append(b"\n")
    if st.unmerged:
        lines.append(b"Unmerged paths:\n")
        if hints:
            lines.append(b'  (use "git restore --staged <file>..." to unstage)\n' if born
                         else b'  (use "git rm --cached <file>..." to unstage)\n')
            if any(code in ("DD", "AU", "UD", "UA", "DU") for _, code in st.unmerged):
                lines.append(b'  (use "git add/rm <file>..." as appropriate to mark resolution)\n')
            else:
                lines.append(b'  (use "git add <file>..." to mark resolution)\n')
        for path, code in st.unmerged:
            lines.append(b"\t" + UNMERGED_LABELS[code].ljust(17).encode() + show(path) + b"\n")
        lines.append(b"\n")
    if st.unstaged:
        lines.append(b"Changes not staged for commit:\n")
        if hints:
            if any(c.status == "D" for c in st.unstaged):
                lines.append(b'  (use "git add/rm <file>..." to update what will be committed)\n')
            else:
                lines.append(b'  (use "git add <file>..." to update what will be committed)\n')
            lines.append(b'  (use "git restore <file>..." to discard changes in working directory)\n')
        for c in st.unstaged:
            lines.append(b"\t" + LABELS[c.status].ljust(12).encode() + show(c.path) + b"\n")
        lines.append(b"\n")
    if st.untracked:
        lines.append(b"Untracked files:\n")
        if hints:
            lines.append(b'  (use "git add <file>..." to include in what will be committed)\n')
        for path in st.untracked:
            lines.append(b"\t" + show(path) + b"\n")
        lines.append(b"\n")
    if st.ignored:
        lines.append(b"Ignored files:\n")
        if hints:
            lines.append(b'  (use "git add -f <file>..." to include in what will be committed)\n')
        for path in st.ignored:
            lines.append(b"\t" + show(path) + b"\n")
        lines.append(b"\n")
    if not show_untracked and st.staged:
        lines.append(b"Untracked files not listed (use -u option to show untracked files)\n")
    if not st.staged:
        if amend and not (st.unstaged or st.unmerged or st.untracked):
            lines.append(b"No changes\n")
        elif st.unstaged or st.unmerged:
            lines.append(b'no changes added to commit (use "git add" and/or "git commit -a")\n')
        elif st.untracked:
            lines.append(b'nothing added to commit but untracked files present (use "git add" to track)\n')
        elif not born:
            lines.append(b'nothing to commit (create/copy files and use "git add" to track)\n')
        elif not show_untracked:
            lines.append(b"nothing to commit (use -u to show untracked files)\n")
        else:
            lines.append(b"nothing to commit, working tree clean\n")
    return b"".join(lines)


def short_status(repo, st, prefix: bytes, porcelain: bool, z: bool, branch: bool) -> bytes:
    lines = []
    end = b"\0" if z else b"\n"
    if branch:
        from pygit.branching import short_branch_header
        lines.append(short_branch_header(repo) + end)

    def show(path: bytes) -> bytes:
        rel = path if porcelain else relative_to_cwd(path, prefix)
        return rel if z else quote_path(rel)

    entries: dict[bytes, list] = {}
    for c in st.staged:
        entries.setdefault(c.path, [" ", " ", None])
        entries[c.path][0] = c.status
        if c.status == "R":
            entries[c.path][2] = c.orig
    for c in st.unstaged:
        entries.setdefault(c.path, [" ", " ", None])
        entries[c.path][1] = c.status
    for path, code in st.unmerged:
        entries[path] = [code[0], code[1], None]
    for path in sorted(entries):
        x, y, orig = entries[path]
        if orig is not None:
            if z:
                lines.append(f"{x}{y} ".encode() + show(path) + b"\0" + show(orig) + b"\0")
            else:
                lines.append(f"{x}{y} ".encode() + show(orig) + b" -> " + show(path) + b"\n")
        else:
            lines.append(f"{x}{y} ".encode() + show(path) + end)
    for path in st.untracked:
        lines.append(b"?? " + show(path) + end)
    for path in st.ignored:
        lines.append(b"!! " + show(path) + end)
    return b"".join(lines)


@command("status")
def cmd_status(args):
    p = parser("status")
    p.add_argument("-s", "--short", action="store_true")
    p.add_argument("--porcelain", nargs="?", const="v1")
    p.add_argument("-b", "--branch", action="store_true")
    p.add_argument("-z", action="store_true")
    p.add_argument("-u", "--untracked-files", nargs="?", const="all", default="normal")
    p.add_argument("--ignored", nargs="?", const="traditional")
    p.add_argument("--no-renames", action="store_true")
    p.add_argument("--long", action="store_true")
    p.add_argument("paths", nargs="*")
    a = p.parse_args(args)
    repo = work_repo()
    if a.porcelain not in (None, "v1", "1"):
        raise GitError(f"unsupported porcelain version '{a.porcelain}'")
    renames = not a.no_renames and repo.config.get_bool("status.renames",
                                                         repo.config.get_bool("diff.renames", True))
    st = compute(repo, a.untracked_files, ignored=bool(a.ignored), detect_renames=renames)
    if a.paths:
        spec = Pathspec(repo, a.paths)
        st.staged = [c for c in st.staged if spec.matches(c.path) or (c.orig and spec.matches(c.orig))]
        st.unstaged = [c for c in st.unstaged if spec.matches(c.path)]
        st.unmerged = [u for u in st.unmerged if spec.matches(u[0])]
        st.untracked = [u for u in st.untracked if spec.matches(u.rstrip(b"/"))]
        st.ignored = [u for u in st.ignored if spec.matches(u.rstrip(b"/"))]
    prefix = cwd_prefix(repo)
    if a.porcelain or a.short or a.z:
        out(short_status(repo, st, prefix, porcelain=bool(a.porcelain) or (a.z and not a.short),
                         z=a.z, branch=a.branch))
    else:
        out(long_status(repo, st, prefix, show_untracked=a.untracked_files != "no"))
    return 0

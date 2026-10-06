"""Stage 4 commands: merge-base, merge-file, merge."""

from __future__ import annotations

import sys
from pathlib import Path

from pygit import revparse, xmerge
from pygit.cli import command, err, out
from pygit.errors import GitError
from pygit.history import is_ancestor, merge_bases
from pygit.repo import find_repo


def _commit_of(repo, spec: str) -> str:
    try:
        return revparse.peel(repo, revparse.resolve(repo, spec), b"commit")
    except GitError:
        raise GitError(f"Not a valid object name {spec}")


@command("merge-base")
def cmd_merge_base(args):
    repo = find_repo()
    all_ = is_anc = False
    revs = []
    for arg in args:
        if arg in ("-a", "--all"):
            all_ = True
        elif arg == "--is-ancestor":
            is_anc = True
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            revs.append(arg)
    if is_anc:
        if len(revs) != 2:
            raise GitError("--is-ancestor takes exactly two commits")
        a, b = (_commit_of(repo, r) for r in revs)
        return 0 if is_ancestor(repo, a, b) else 1
    if len(revs) < 2:
        raise GitError("usage: git merge-base [-a | --all] <commit> <commit>...")
    commits = [_commit_of(repo, r) for r in revs]
    bases = merge_bases(repo, commits[0], commits[1])
    for extra in commits[2:]:
        # "merge-base A B C": bases of A and a hypothetical merge of B and C.
        more = merge_bases(repo, commits[0], extra)
        bases = sorted(set(bases) | set(more))
        from pygit.history import CommitCache, ancestors
        cache = CommitCache(repo)
        redundant = {x for x in bases for y in bases if x != y and x in ancestors(repo, [y], cache)}
        bases = [x for x in bases if x not in redundant]
    if not bases:
        return 1
    for b in (bases if all_ else bases[:1]):
        out(b.encode() + b"\n")
    return 0


@command("merge-file")
def cmd_merge_file(args):
    to_stdout = quiet = False
    style = None
    favor = xmerge.FAVOR_NONE
    labels = []
    marker = xmerge.DEFAULT_MARKER_SIZE
    files = []
    it = iter(args)
    for arg in it:
        if arg in ("-p", "--stdout"):
            to_stdout = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg == "--diff3":
            style = xmerge.STYLE_DIFF3
        elif arg == "--zdiff3":
            style = xmerge.STYLE_ZEALOUS_DIFF3
        elif arg == "--ours":
            favor = xmerge.FAVOR_OURS
        elif arg == "--theirs":
            favor = xmerge.FAVOR_THEIRS
        elif arg == "--union":
            favor = xmerge.FAVOR_UNION
        elif arg == "-L":
            labels.append(next(it))
        elif arg.startswith("--marker-size="):
            marker = int(arg.split("=", 1)[1])
        elif arg.startswith("-") and arg != "-":
            raise GitError(f"unknown option '{arg}'")
        else:
            files.append(arg)
    if len(files) != 3:
        raise GitError("usage: git merge-file [<options>] [-L <name1> [-L <orig> [-L <name2>]]] "
                       "<file1> <orig-file> <file2>")
    if style is None:
        style = _config_style()
    names = labels + files[len(labels):]
    cur, base, other = (Path(f).read_bytes() for f in files)
    for f, data in zip(files, (cur, base, other)):
        if b"\0" in data[:8000]:
            err(f"error: Cannot merge binary files: {f}\n")
            return 255
    merged, conflicts = xmerge.merge(base, cur, other, names[0], names[2], names[1],
                                     level=xmerge.ZEALOUS_ALNUM, style=style, favor=favor,
                                     marker_size=marker)
    if to_stdout:
        out(merged)
    else:
        Path(files[0]).write_bytes(merged)
    return min(conflicts, 127)


MERGE_FILES = ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE", "AUTO_MERGE")


def merge_label(repo, spec: str) -> tuple[str, str]:
    """(message fragment, conflict-marker label) for what is being merged,
    as fmt_merge_msg names it: branch 'x', remote-tracking branch 'o/x',
    tag 'v1', or commit '<as typed>'."""
    from pygit.refs import Refs
    name = spec
    for suffix in ("^0", "~0"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    refs = Refs(repo)
    if refs.exists("refs/heads/" + name):
        return f"branch '{name}'", spec
    if name.startswith("refs/heads/") and refs.exists(name):
        return f"branch '{name[len('refs/heads/'):]}'", spec
    if refs.exists("refs/remotes/" + name):
        return f"remote-tracking branch '{name}'", spec
    if refs.exists("refs/tags/" + name):
        return f"tag '{name}'", spec
    return f"commit '{spec}'", spec


def _stat_between(repo, old_tree, new_tree) -> bytes:
    from pygit import diffout
    from pygit.treediff import diff_maps, summary_lines, tree_entries
    pairs = diff_maps(repo.odb, tree_entries(repo.odb, old_tree), tree_entries(repo.odb, new_tree))
    if not pairs:
        return b""
    return diffout.stat(pairs, diffout.Contents(repo)) + summary_lines(pairs)


def _write_state(repo, name: str, data: bytes) -> None:
    from pygit.lockfile import write_locked
    write_locked(repo.gitdir / name, data)


def _clear_merge_state(repo) -> None:
    for name in MERGE_FILES:
        try:
            (repo.gitdir / name).unlink()
        except FileNotFoundError:
            pass


@command("merge")
def cmd_merge(args):
    from pygit.branching import current_branch
    from pygit.checkout import CheckoutConflict, switch_trees
    from pygit.ident import ident
    from pygit.index import Index
    from pygit.objects import Commit
    from pygit.refs import Refs
    from pygit.status import head_tree_entries

    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    ff_mode = "allow"
    commit = True
    message = None
    quiet = False
    stat = repo.config.get_bool("merge.stat", True)
    abort = cont = False
    targets = []
    it = iter(args)
    for arg in it:
        if arg == "--no-ff":
            ff_mode = "never"
        elif arg == "--ff-only":
            ff_mode = "only"
        elif arg == "--ff":
            ff_mode = "allow"
        elif arg == "--no-commit":
            commit = False
        elif arg == "--commit":
            commit = True
        elif arg in ("-m", "--message"):
            message = next(it)
        elif arg in ("--no-edit", "--edit", "-e"):
            pass
        elif arg in ("-q", "--quiet"):
            quiet = True
            stat = False
        elif arg in ("-n", "--no-stat"):
            stat = False
        elif arg == "--stat":
            stat = True
        elif arg == "--abort":
            abort = True
        elif arg == "--continue":
            cont = True
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            targets.append(arg)
    refs = Refs(repo)
    if abort:
        if not (repo.gitdir / "MERGE_HEAD").is_file():
            raise GitError("There is no merge to abort (MERGE_HEAD missing).")
        _abort(repo)
        return 0
    if cont:
        if not (repo.gitdir / "MERGE_HEAD").is_file():
            raise GitError("There is no merge in progress (MERGE_HEAD missing).")
        from pygit.commands.committing import cmd_commit
        return cmd_commit([])
    idx = Index.read(repo)
    if idx.has_conflicts():
        err("error: Merging is not possible because you have unmerged files.\n"
            "hint: Fix them up in the work tree, and then use 'git add/rm <file>'\n"
            "hint: as appropriate to mark resolution and make a commit.\n"
            "fatal: Exiting because of an unresolved conflict.\n")
        return 128
    if (repo.gitdir / "MERGE_HEAD").is_file():
        raise GitError("You have not concluded your merge (MERGE_HEAD exists).\n"
                       "Please, commit your changes before you merge.")
    if not targets:
        raise GitError("No remote for the current branch.")
    if len(targets) > 1:
        raise GitError("pygit merge does not do octopus merges")
    spec = targets[0]
    try:
        theirs = revparse.peel(repo, revparse.resolve(repo, spec), b"commit")
    except GitError:
        err(f"merge: {spec} - not something we can merge\n")
        return 1
    head, _ = refs.resolve("HEAD")
    branch = current_branch(repo)
    what, label = merge_label(repo, spec)
    reflog_name = spec
    if head is None:
        # Merging into an unborn branch: just point it at theirs.
        switch_trees(repo, None, Commit.parse(repo.odb.read(theirs)[1]).tree)
        refs.update("HEAD", theirs, f"initial pull")
        return 0
    if is_ancestor(repo, theirs, head):
        out(b"Already up to date.\n")
        return 0
    head_c = Commit.parse(repo.odb.read(head)[1])
    theirs_c = Commit.parse(repo.odb.read(theirs)[1])
    can_ff = is_ancestor(repo, head, theirs)
    if ff_mode == "only" and not can_ff:
        err("hint: Diverging branches can't be fast-forwarded, you need to either:\n"
            "hint:\n"
            "hint: \tgit merge --no-ff\n"
            "hint:\n"
            "hint: or:\n"
            "hint:\n"
            "hint: \tgit rebase\n"
            "hint:\n"
            "hint: Disable this message with \"git config set advice.diverging false\"\n")
        raise GitError("Not possible to fast-forward, aborting.")
    if can_ff and ff_mode != "never":
        out(f"Updating {revparse.short_id(repo, head)}..{revparse.short_id(repo, theirs)}\n".encode())
        try:
            switch_trees(repo, head_c.tree, theirs_c.tree, action="merge", hint="merge")
        except CheckoutConflict as e:
            err(e.message)
            return 1
        out(b"Fast-forward\n")
        _write_state(repo, "ORIG_HEAD", (head + "\n").encode())
        refs.update("HEAD", theirs, f"merge {reflog_name}: Fast-forward")
        if stat:
            out(_stat_between(repo, head_c.tree, theirs_c.tree))
        return 0

    # A real merge. The index must match HEAD.
    head_map = head_tree_entries(repo)
    index_map = {e.path: (e.mode, e.oid) for e in idx.sorted_entries()}
    dirty_index = sorted(p for p in set(head_map) | set(index_map) if head_map.get(p) != index_map.get(p))
    if dirty_index:
        err("error: Your local changes to the following files would be overwritten by merge:\n" +
            "".join(f"  {p.decode('utf-8', 'replace')}\n" for p in dirty_index) +
            "Merge with strategy ort failed.\n")
        return 2
    from pygit import merge_ort
    from pygit.history import merge_bases as mb
    bases = mb(repo, head, theirs)
    style = _config_style()
    ancestor = (revparse.short_id(repo, bases[0]) if len(bases) == 1
                else "merged common ancestors" if bases else "empty tree")

    def factory(l1, l2):
        return merge_ort.Merger(repo, l1, l2, ancestor, style=style)

    base_tree = merge_ort.virtual_base(repo, bases, factory)
    merger = merge_ort.Merger(repo, "HEAD", label, ancestor, style=style)
    res = merger.merge_trees(base_tree, head_c.tree, theirs_c.tree)

    # Apply to the index and work tree; refuse if local changes are in the way.
    from pygit import worktree
    from pygit.checkout import conflict_message, remove_file, write_file
    from pygit.index import IndexEntry
    result_paths = {p for p, s in res.entries}
    to_write, to_remove = [], []
    for p in sorted(result_paths | set(head_map)):
        if p in res.worktree:
            to_write.append(p)
        elif (p, 0) in res.entries:
            if res.entries[(p, 0)] != head_map.get(p):
                to_write.append(p)
        elif p in head_map and p not in result_paths:
            to_remove.append(p)
    local, untracked = [], []
    for p in to_write + to_remove:
        e = idx.get(p)
        st = worktree.lstat(repo, p)
        if e is not None:
            if st is None or worktree.is_modified(repo, e, st):
                local.append(p)
        elif st is not None and not (worktree.fs_path(repo, p).is_dir()):
            untracked.append(p)
    if local or untracked:
        err(conflict_message(sorted(local), sorted(untracked), "merge", "merge") +
            "Merge with strategy ort failed.\n")
        return 2
    _write_state(repo, "ORIG_HEAD", (head + "\n").encode())
    new_idx = Index(idx.path)
    for (p, s), (m, o) in res.entries.items():
        old = idx.get(p)
        if s == 0 and old is not None and old.oid == o and old.mode == m:
            new_idx.entries[(p, 0)] = old
        else:
            new_idx.entries[(p, s)] = IndexEntry(path=p, oid=o, mode=m, stage=s)
    for p in to_remove:
        remove_file(repo, p)
    for p in to_write:
        if p in res.worktree:
            mode, data = res.worktree[p]
            oid = repo.odb.write(b"blob", data)
            write_file(repo, p, mode, oid)
        else:
            mode, oid = res.entries[(p, 0)]
            st = write_file(repo, p, mode, oid)
            new_idx.entries[(p, 0)].set_stat(st)
    new_idx.write()
    for line in res.sorted_messages():
        out(line.encode() + b"\n")
    msg = message or f"Merge {what}" + ("" if branch in (None, "master", "main") else f" into {branch}")
    if not res.clean:
        conflicts = sorted({p for p, s in res.entries if s})
        _write_state(repo, "MERGE_HEAD", (theirs + "\n").encode())
        _write_state(repo, "MERGE_MODE", b"no-ff" if ff_mode == "never" else b"")
        body = msg.rstrip("\n") + "\n\n# Conflicts:\n" + "".join(
            f"#\t{p.decode('utf-8', 'replace')}\n" for p in conflicts)
        _write_state(repo, "MERGE_MSG", body.encode())
        out(b"Automatic merge failed; fix conflicts and then commit the result.\n")
        return 1
    if not commit:
        _write_state(repo, "MERGE_HEAD", (theirs + "\n").encode())
        _write_state(repo, "MERGE_MODE", b"no-ff" if ff_mode == "never" else b"")
        _write_state(repo, "MERGE_MSG", (msg.rstrip("\n") + "\n").encode())
        out(b"Automatic merge went well; stopped before committing as requested\n")
        return 0
    tree = new_idx.write_tree(repo.odb)
    from pygit.pretty import cleanup_message
    c = Commit(tree, [head, theirs], ident(repo, "author"), ident(repo, "committer"),
               cleanup_message(msg.encode(), "strip"))
    oid = repo.odb.write(b"commit", c.serialize())
    refs.update("HEAD", oid, f"merge {reflog_name}: Merge made by the 'ort' strategy.")
    out(b"Merge made by the 'ort' strategy.\n")
    if stat:
        out(_stat_between(repo, head_c.tree, tree))
    return 0


def _abort(repo) -> None:
    """`merge --abort`: back to HEAD for every path the merge touched."""
    from pygit.checkout import remove_file, write_file
    from pygit.index import Index, IndexEntry
    from pygit.status import head_tree_entries
    head_map = head_tree_entries(repo)
    idx = Index.read(repo)
    touched = {p for p, s in idx.entries if s} | \
              {e.path for e in idx.sorted_entries() if head_map.get(e.path) != (e.mode, e.oid)} | \
              {p for p in head_map if idx.get(p) is None}
    for p in sorted(touched):
        idx.remove(p)
        if p in head_map:
            mode, oid = head_map[p]
            st = write_file(repo, p, mode, oid)
            e = IndexEntry(path=p, oid=oid, mode=mode)
            e.set_stat(st)
            idx.add(e)
        else:
            remove_file(repo, p)
    idx.write()
    _clear_merge_state(repo)


def _config_style() -> int:
    try:
        repo = find_repo()
    except GitError:
        from pygit import config
        cfg = config.Config(config.global_config_paths())
    else:
        cfg = repo.config
    v = (cfg.get("merge.conflictStyle") or "merge").lower()
    return {"diff3": xmerge.STYLE_DIFF3, "zdiff3": xmerge.STYLE_ZEALOUS_DIFF3}.get(v, xmerge.STYLE_MERGE)

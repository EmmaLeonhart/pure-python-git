"""Stage 3: `diff` between the work tree, the index and commits."""

from __future__ import annotations

import os

from pygit import diffout, revparse, worktree
from pygit.cli import command, out
from pygit.errors import GitError
from pygit.history import merge_bases
from pygit.index import Index, mode_from_stat
from pygit.objects import Commit, hash_bytes
from pygit.pathspec import Pathspec
from pygit.repo import find_repo
from pygit.status import head_commit
from pygit.treediff import diff_maps, tree_entries


def _tree_of(repo, spec: str) -> str:
    try:
        return revparse.peel(repo, revparse.resolve(repo, spec), b"tree")
    except GitError:
        raise GitError(f"ambiguous argument '{spec}': unknown revision or path not in the working tree.\n"
                       "Use '--' to separate paths from revisions, like this:\n"
                       "'git <command> [<revision>...] -- [<file>...]'")


def _index_map(idx: Index) -> dict:
    return {e.path: (e.mode, e.oid) for e in idx.sorted_entries() if e.stage == 0}


def _worktree_map(repo, idx: Index, paths: set[bytes], contents: diffout.Contents,
                  base: dict) -> dict:
    """Work-tree side for `paths`: unchanged files keep their index id;
    changed ones are hashed (without writing objects)."""
    filemode = repo.config.get_bool("core.fileMode", os.name != "nt")
    out_map = {}
    for path in sorted(paths):  # path order, so warnings come out as git's do
        st = worktree.lstat(repo, path)
        if st is None:
            continue
        import stat as statmod
        if statmod.S_ISDIR(st.st_mode):
            e = idx.get(path)
            if e is not None and e.mode == 0o160000:
                out_map[path] = (e.mode, e.oid)
            continue
        e = idx.get(path)
        old_mode = base.get(path, (None,))[0]
        mode = mode_from_stat(st, filemode, e.mode if e else old_mode)
        if e is not None and not worktree.is_modified(repo, e, st):
            out_map[path] = (mode, e.oid)
            continue
        # diff reads work-tree files with core.safecrlf warnings on, as git does.
        data = worktree.read_worktree_blob(repo, path, st, e.oid if e else None, warn=True)
        oid = hash_bytes(b"blob", data)
        contents.extra[oid] = data
        out_map[path] = (mode, oid)
    return out_map


def _filter(m: dict, spec: Pathspec) -> dict:
    return {p: v for p, v in m.items() if spec.matches(p)} if spec else m


@command("diff")
def cmd_diff(args):
    repo = find_repo()
    cached = False
    fmt = "patch"
    context = 3
    renames = repo.config.get_bool("diff.renames", True)
    exit_code = quiet = False
    z = False
    full_index = False
    stat_width = None
    revs, paths = [], []
    it = iter(args)
    seen_dashdash = False
    for arg in it:
        if seen_dashdash:
            paths.append(arg)
        elif arg == "--":
            seen_dashdash = True
        elif arg in ("--cached", "--staged"):
            cached = True
        elif arg.startswith("-U"):
            context = int(arg[2:])
        elif arg.startswith("--unified="):
            context = int(arg.split("=", 1)[1])
        elif arg == "--stat":
            fmt = "stat"
        elif arg.startswith("--stat="):
            fmt = "stat"
            stat_width = int(arg.split("=", 1)[1].split(",")[0])
        elif arg == "--numstat":
            fmt = "numstat"
        elif arg == "--shortstat":
            fmt = "shortstat"
        elif arg == "--name-only":
            fmt = "name-only"
        elif arg == "--name-status":
            fmt = "name-status"
        elif arg in ("--no-renames",):
            renames = False
        elif arg in ("-M", "--find-renames"):
            renames = True
        elif arg == "--exit-code":
            exit_code = True
        elif arg in ("--quiet",):
            quiet = exit_code = True
        elif arg == "-z":
            z = True
        elif arg == "--full-index":
            full_index = True
        elif arg in ("-p", "--patch", "--no-color", "--color=never"):
            pass
        elif arg.startswith("-"):
            raise GitError(f"invalid option: {arg}")
        else:
            revs.append(arg)
    # Without "--", trailing arguments that are not revisions are paths.
    if not seen_dashdash:
        rv = []
        for r in revs:
            if paths:
                paths.append(r)
                continue
            try:
                # A range is checked by resolving its ends ("../x" is a path);
                # ".." alone, with no ends, is a path too.
                if r.strip(".") == "":
                    raise GitError("not a revision")
                for end in (r.replace("...", "..").split("..") if ".." in r else [r]):
                    revparse.resolve(repo, end or "HEAD")
                rv.append(r)
            except GitError:
                if repo.worktree is not None and os.path.exists(r):  # relative to the cwd
                    paths.append(r)
                else:
                    raise GitError(f"ambiguous argument '{r}': unknown revision or path not in the working tree.\n"
                                   "Use '--' to separate paths from revisions, like this:\n"
                                   "'git <command> [<revision>...] -- [<file>...]'")
        revs = rv
    spec = Pathspec(repo, paths)
    contents = diffout.Contents(repo)

    # Expand ranges.
    if len(revs) == 1 and "..." in revs[0]:
        left, right = revs[0].split("...", 1)
        l = revparse.peel(repo, revparse.resolve(repo, left or "HEAD"), b"commit")
        r = revparse.peel(repo, revparse.resolve(repo, right or "HEAD"), b"commit")
        bases = merge_bases(repo, l, r)
        if not bases:
            raise GitError(f"{revs[0]}: no merge base")
        revs = [bases[0], r]
    elif len(revs) == 1 and ".." in revs[0]:
        left, right = revs[0].split("..", 1)
        revs = [left or "HEAD", right or "HEAD"]

    idx = Index.read(repo) if repo.worktree is not None or cached else None
    if len(revs) == 2:
        old = _filter(tree_entries(repo.odb, _tree_of(repo, revs[0])), spec)
        new = _filter(tree_entries(repo.odb, _tree_of(repo, revs[1])), spec)
    elif len(revs) > 2:
        raise GitError("pygit diff takes at most two revisions")
    elif cached:
        base = revs[0] if revs else ("HEAD" if head_commit(repo) else None)
        old = _filter(tree_entries(repo.odb, _tree_of(repo, base)) if base else {}, spec)
        new = _filter(_index_map(idx), spec)
    elif revs:
        if repo.worktree is None:
            raise GitError("this operation must be run in a work tree")
        old = _filter(tree_entries(repo.odb, _tree_of(repo, revs[0])), spec)
        tracked = set(old) | {p for p in _index_map(idx) if spec.matches(p)}
        new = _worktree_map(repo, idx, tracked, contents, old)
    else:
        if repo.worktree is None:
            raise GitError("this operation must be run in a work tree")
        old = _filter(_index_map(idx), spec)
        worktree.refresh(repo, idx)
        new = _worktree_map(repo, idx, set(old), contents, old)
    unmerged = set()
    if idx is not None and len(revs) < 2:
        unmerged = {p for p in idx.conflicted_paths() if spec.matches(p)}
        old = {p: v for p, v in old.items() if p not in unmerged}
        new = {p: v for p, v in new.items() if p not in unmerged}
    pairs = diff_maps(repo.odb, old, new, renames=renames and not (not revs and not cached))
    combined_out = []
    if unmerged:
        from pygit.treediff import FilePair
        work_tree_diff = not revs and not cached
        extra = []
        for p in sorted(unmerged):
            ours, theirs = idx.get(p, 2), idx.get(p, 3)
            if work_tree_diff and fmt == "patch" and ours is not None and theirs is not None:
                # Both sides present: a dense combined diff against them,
                # printed before the other pairs as run_diff_files does.
                combined_out.append(_combined_for_worktree(repo, p, ours, theirs))
                continue
            extra.append(FilePair("U", p, p))
            if work_tree_diff and ours is not None:
                # Also "ours" (stage 2) against the work tree.
                wt = _worktree_map(repo, idx, {p}, contents, {p: (ours.mode, ours.oid)})
                cur = wt.get(p)
                if cur is None:
                    extra.append(FilePair("D", p, None, ours.mode, 0, ours.oid, None))
                elif cur != (ours.mode, ours.oid):
                    extra.append(FilePair("M", p, p, ours.mode, cur[0], ours.oid, cur[1]))
        # A path's "U" entry comes before its other entry (stable sort).
        pairs = sorted(extra + pairs, key=lambda x: (x.path, x.status != "U"))
    if not quiet:
        if fmt == "patch":
            out(b"".join(combined_out))
            out(diffout.patch(repo, pairs, contents, context, full_index=full_index))
        elif fmt == "stat":
            out(diffout.stat(pairs, contents, stat_width))
        elif fmt == "shortstat":
            out(diffout.shortstat_for(pairs, contents))
        elif fmt == "numstat":
            out(diffout.numstat(pairs, contents, z))
        elif fmt == "name-only":
            out(diffout.name_only(pairs, z))
        elif fmt == "name-status":
            out(diffout.name_status(pairs, z))
    if exit_code:
        return 1 if pairs or combined_out else 0
    return 0


def _combined_for_worktree(repo, path: bytes, ours, theirs) -> bytes:
    from pygit.combined import combined_patch
    st = worktree.lstat(repo, path)
    if st is None:
        mode, data = 0, b""
    else:
        filemode = repo.config.get_bool("core.fileMode", os.name != "nt")
        mode = mode_from_stat(st, filemode, ours.mode)
        data = worktree.read_worktree_blob(repo, path, st)
    parents = [(ours.mode, ours.oid), (theirs.mode, theirs.oid)]
    pdata = [repo.odb.read(ours.oid)[1], repo.odb.read(theirs.oid)[1]]
    return combined_patch(repo, path, None, mode, data, parents, pdata, working_tree=True)

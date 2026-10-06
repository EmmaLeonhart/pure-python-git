"""Moving the index and work tree between trees (unpack-trees' two-way merge).

Per path, with H the current tree, T the target and I the index:
  - H == T: the index entry and file are left alone (local changes carry over);
  - I == H and the file is clean: the path is updated to T;
  - I == T: nothing to do;
  - otherwise the local change would be lost, and the whole checkout stops.
An untracked file in the way of a file T adds also stops it, unless it is
ignored: git treats ignored files as expendable.
"""

from __future__ import annotations

import os
import stat as statmod
from pathlib import Path

from pygit import worktree
from pygit.errors import GitError
from pygit.ignore import IgnoreRules
from pygit.index import Index, IndexEntry, walk_tree


class CheckoutConflict(GitError):
    pass


def tree_map(odb, tree: str | None) -> dict[bytes, tuple[int, str]]:
    return {p: (m, o) for p, m, o in walk_tree(odb, tree)} if tree else {}


def write_file(repo, path: bytes, mode: int, oid: str) -> os.stat_result:
    """Write a blob to the work tree (as a symlink when the mode says so)."""
    fp = worktree.fs_path(repo, path)
    # A directory or file in the way of the parent directories goes first.
    parent = fp.parent
    _clear_for_dir(repo, parent)
    if fp.is_dir() and not fp.is_symlink():
        _rmtree_if_empty_dirs(fp)
    elif fp.exists() or fp.is_symlink():
        _unlink(fp)
    data = repo.odb.read(oid)[1]
    if mode == 0o160000:
        fp.mkdir(parents=True, exist_ok=True)
        return os.lstat(fp)
    symlinks = repo.config.get_bool("core.symlinks", os.name != "nt")
    if mode == 0o120000 and symlinks:
        os.symlink(os.fsdecode(data), fp)
    else:
        fd = os.open(fp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0),
                     0o777 if mode == 0o100755 else 0o666)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        if mode == 0o100755 and os.name != "nt":
            os.chmod(fp, (os.stat(fp).st_mode | 0o111) & ~_umask())
        elif mode == 0o100644 and os.name != "nt":
            os.chmod(fp, os.stat(fp).st_mode & ~0o111)
    return os.lstat(fp)


def _umask() -> int:
    m = os.umask(0)
    os.umask(m)
    return m


def _unlink(fp: Path) -> None:
    try:
        fp.unlink()
    except PermissionError:
        os.chmod(fp, 0o666)
        fp.unlink()


def _clear_for_dir(repo, d: Path) -> None:
    """Make sure `d` can be a directory: replace a file in the way."""
    root = repo.worktree
    chain = []
    cur = d
    while cur != root and root in cur.parents:
        chain.append(cur)
        cur = cur.parent
    for p in reversed(chain):
        if p.is_symlink() or (p.exists() and not p.is_dir()):
            _unlink(p)
    d.mkdir(parents=True, exist_ok=True)


def _rmtree_if_empty_dirs(d: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(d, topdown=False):
        if filenames:
            raise GitError(f"cannot replace directory '{d}': it is not empty")
        os.rmdir(dirpath)


def remove_file(repo, path: bytes) -> None:
    fp = worktree.fs_path(repo, path)
    if fp.is_symlink() or fp.is_file():
        _unlink(fp)
    elif fp.is_dir():
        try:
            fp.rmdir()  # an empty submodule directory
        except OSError:
            pass
    d = fp.parent
    while d != repo.worktree and repo.worktree in d.parents:
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


def entry_after_write(repo, path: bytes, mode: int, oid: str, st) -> IndexEntry:
    e = IndexEntry(path=path, oid=oid, mode=mode)
    e.set_stat(st)
    return e


def plan(repo, idx: Index, old: dict, new: dict, force: bool = False):
    """Decide what to do per path. Returns (updates, removals, errors) where
    errors = (local_changes, untracked) lists of paths."""
    updates, removals = [], []
    local, untracked = [], []
    rules = None
    for path in sorted(set(old) | set(new)):
        h, t = old.get(path), new.get(path)
        e = idx.get(path)
        unmerged = any(idx.get(path, s) for s in (1, 2, 3))
        if force:
            if t is None:
                if e is not None or h is not None or unmerged:
                    removals.append(path)
            else:
                updates.append((path, t))
            continue
        if unmerged:
            local.append(path)  # callers refuse an unmerged index first
            continue
        i = (e.mode, e.oid) if e else None
        st = worktree.lstat(repo, path)
        if e is not None:
            dirty = st is None or worktree.is_modified(repo, e, st)
        else:
            dirty = False
        if h == t:
            continue  # carried over untouched
        if i == t:
            continue  # already what the target wants
        if i == h and not dirty:
            if t is None:
                removals.append(path)
            else:
                if e is None and st is not None and not statmod.S_ISDIR(st.st_mode):
                    # Untracked (H and I lack it): would be overwritten.
                    data = worktree.read_worktree_blob(repo, path, st)
                    from pygit.objects import hash_bytes
                    if hash_bytes(b"blob", data) != t[1]:
                        if rules is None:
                            rules = IgnoreRules(repo)
                        if not rules.is_ignored(path, False):
                            untracked.append(path)
                            continue
                updates.append((path, t))
            continue
        if i == h and dirty and e is not None and st is None and t is None:
            # Deleted locally and deleted in the target: fine.
            removals.append(path)
            continue
        local.append(path)
    # Untracked files inside a directory that the target replaces with a file
    # are left to write_file, which refuses non-empty directories.
    return updates, removals, (local, untracked)


def conflict_message(local, untracked, action="checkout", hint="switch branches") -> str:
    parts = []
    if local:
        parts.append(f"error: Your local changes to the following files would be overwritten by {action}:\n" +
                     "".join(f"\t{os.fsdecode(p)}\n" for p in local) +
                     f"Please commit your changes or stash them before you {hint}.\n")
    if untracked:
        parts.append(f"error: The following untracked working tree files would be overwritten by {action}:\n" +
                     "".join(f"\t{os.fsdecode(p)}\n" for p in untracked) +
                     f"Please move or remove them before you {hint}.\n")
    parts.append("Aborting\n")
    return "".join(parts)


def apply(repo, idx: Index, updates, removals) -> None:
    for path in removals:
        idx.remove(path)
        remove_file(repo, path)
    for path, (mode, oid) in updates:
        st = write_file(repo, path, mode, oid)
        idx.add(entry_after_write(repo, path, mode, oid, st))


def switch_trees(repo, old_tree: str | None, new_tree: str | None, force: bool = False,
                 action: str = "checkout", hint: str = "switch branches") -> Index:
    """Move index and work tree from old_tree to new_tree; returns the new index
    (already written). Raises CheckoutConflict with git's message."""
    idx = Index.read(repo)
    old = tree_map(repo.odb, old_tree)
    new = tree_map(repo.odb, new_tree)
    updates, removals, (local, untracked) = plan(repo, idx, old, new, force)
    if local or untracked:
        raise CheckoutConflict(conflict_message(local, untracked, action, hint), code=1)
    if force:
        # Drop conflict stages and anything not in the target.
        for key in list(idx.entries):
            if key[1] or key[0] not in new:
                if key[0] not in new and key[0] not in removals and key[0] in old:
                    removals.append(key[0])
                idx.entries.pop(key, None)
    apply(repo, idx, updates, removals)
    idx.write()
    return idx


def checkout_index(repo, old_idx: Index, new_idx: Index) -> None:
    """Make the work tree match new_idx, for files that changed between the two."""
    for path in sorted(old_idx.paths() - new_idx.paths()):
        remove_file(repo, path)
    for e in new_idx.sorted_entries():
        o = old_idx.get(e.path)
        st = worktree.lstat(repo, e.path)
        if o is not None and o.oid == e.oid and o.mode == e.mode and st is not None:
            continue
        st = write_file(repo, e.path, e.mode, e.oid)
        e.set_stat(st)

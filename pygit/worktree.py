"""The work tree: walking files, hashing them, comparing them to the index."""

from __future__ import annotations

import os
import stat as statmod
from pathlib import Path

from pygit.index import Index, IndexEntry, mode_from_stat
from pygit.objects import hash_bytes


def fs_path(repo, path: bytes) -> Path:
    return repo.worktree / os.fsdecode(path)


def read_worktree_blob(repo, path: bytes, st: os.stat_result | None = None,
                       index_oid: str | None = None, warn: bool = False) -> bytes:
    """The bytes git would store for a work-tree file: the link target for
    symlinks, else the file after the clean (line-ending) conversion.
    `index_oid` is the path's index blob, for git's rule that an index
    version containing CR keeps "auto" files unconverted."""
    p = fs_path(repo, path)
    st = st or os.lstat(p)
    if statmod.S_ISLNK(st.st_mode):
        return os.fsencode(os.readlink(p))
    from pygit.convert import converter
    has_cr = (lambda: b"\r" in repo.odb.read(index_oid)[1]) if index_oid else None
    return converter(repo).to_git(path, p.read_bytes(), has_cr, warn)


def lstat(repo, path: bytes):
    try:
        return os.lstat(fs_path(repo, path))
    except (FileNotFoundError, NotADirectoryError):
        return None


def entry_for_file(repo, path: bytes, old: IndexEntry | None = None, write: bool = True) -> IndexEntry:
    """Hash a work-tree file (writing the blob) and build its index entry."""
    st = os.lstat(fs_path(repo, path))
    filemode = repo.config.get_bool("core.fileMode", os.name != "nt")
    mode = mode_from_stat(st, filemode, old.mode if old else None)
    if old is not None and old.mode == 0o120000 and not statmod.S_ISLNK(st.st_mode) \
            and not repo.config.get_bool("core.symlinks", os.name != "nt"):
        mode = 0o120000  # core.symlinks=false: a checked-out link is a plain file
    data = read_worktree_blob(repo, path, st, old.oid if old else None, warn=write)
    oid = repo.odb.write(b"blob", data) if write else hash_bytes(b"blob", data)
    e = IndexEntry(path=path, oid=oid, mode=mode)
    e.set_stat(st)
    return e


def is_modified(repo, e: IndexEntry, st: os.stat_result | None = None) -> bool:
    """Does the work-tree file differ from its index entry?"""
    if st is None:
        st = lstat(repo, e.path)
    if st is None:
        return True
    if e.mode == 0o160000:
        return False  # submodules: not tracked at this level
    filemode = repo.config.get_bool("core.fileMode", os.name != "nt")
    if statmod.S_ISDIR(st.st_mode):
        return True
    if filemode and e.mode in (0o100644, 0o100755) and mode_from_stat(st, True) != e.mode:
        return True
    if e.stat_matches(st) and not _racy(repo, e):
        return False
    return hash_bytes(b"blob", read_worktree_blob(repo, e.path, st, e.oid)) != e.oid


def _racy(repo, e: IndexEntry) -> bool:
    """Entry written in the same second as the index: stat can't be trusted."""
    try:
        ist = os.stat(repo.gitdir / "index")
    except FileNotFoundError:
        return True
    return e.mtime[0] >= int(ist.st_mtime)


def refresh(repo, idx: Index) -> bool:
    """Update stat data of unchanged entries (like `git update-index --refresh`).

    Returns True if anything changed and the index should be written.
    """
    changed = False
    for e in idx.entries.values():
        if e.stage:
            continue
        st = lstat(repo, e.path)
        if st is None or statmod.S_ISDIR(st.st_mode):
            continue
        if e.stat_matches(st) and not _racy(repo, e):
            continue
        if not is_modified(repo, e, st):
            e.set_stat(st)
            changed = True
    return changed


def walk_files(repo, rules=None, start: bytes = b""):
    """Yield (path, is_dir_marker) of every work-tree file under `start`.

    Ignored directories are pruned (files in them are never yielded), the
    `.git` directory is skipped, and so are nested repositories, which are
    yielded as a single "dir/" entry.
    """
    root = repo.worktree
    stack = [start]
    while stack:
        d = stack.pop()
        full = root / os.fsdecode(d) if d else root
        try:
            names = sorted(os.listdir(full), key=os.fsencode)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        subdirs = []
        for name in names:
            bname = os.fsencode(name)
            if bname == b".git":
                continue
            path = d + b"/" + bname if d else bname
            st = os.lstat(full / name)
            if statmod.S_ISDIR(st.st_mode):
                if (full / name / ".git").exists():
                    yield path, True
                    continue
                if rules is not None and rules.is_ignored_here(path, True):
                    continue
                subdirs.append(path)
            else:
                yield path, False
        stack.extend(reversed(subdirs))


def untracked(repo, idx: Index, rules, all_files: bool = False, include_ignored: bool = False):
    """Untracked paths as `git status` lists them, sorted.

    Returns (untracked, ignored). A directory containing no tracked files is
    collapsed to "dir/" unless `all_files`. Directories with no untracked
    content at all are not listed.
    """
    tracked = idx.paths()
    tracked_dirs = set()
    for p in tracked:
        parts = p.split(b"/")
        for i in range(1, len(parts)):
            tracked_dirs.add(b"/".join(parts[:i]))
    out_untracked = []
    out_ignored = []

    def scan(d: bytes):
        """Returns (untracked entries, ignored entries) under directory d."""
        full = repo.worktree / os.fsdecode(d) if d else repo.worktree
        try:
            names = sorted(os.listdir(full), key=os.fsencode)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return [], []
        u, ig = [], []
        for name in names:
            bname = os.fsencode(name)
            if bname == b".git":
                continue
            path = d + b"/" + bname if d else bname
            st = os.lstat(full / name)
            is_dir = statmod.S_ISDIR(st.st_mode)
            if not is_dir:
                if path in tracked:
                    continue
                if rules.is_ignored_here(path, False):
                    if include_ignored:
                        ig.append(path)
                else:
                    u.append(path)
                continue
            if path in tracked:  # gitlink
                continue
            if rules.is_ignored_here(path, True):
                if include_ignored:
                    if all_files:
                        ig.extend(p for p, _ in walk_files(repo, None, path))
                    else:
                        ig.append(path + b"/")
                continue
            if (full / name / ".git").exists() and path not in tracked_dirs:
                u.append(path + b"/")
                continue
            su, sig = scan(path)
            if path in tracked_dirs or all_files:
                u.extend(su)
                ig.extend(sig)
            else:
                # An untracked dir is collapsed; ignored files inside it are
                # still listed individually.
                if su:
                    u.append(path + b"/")
                    ig.extend(sig)
                elif sig:
                    # git shows a directory holding only ignored files as "dir/".
                    ig.append(path + b"/")
        return u, ig

    u, ig = scan(b"")
    out_untracked.extend(u)
    out_ignored.extend(ig)
    return sorted(out_untracked), sorted(out_ignored)

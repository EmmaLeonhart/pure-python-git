"""Pathspecs: command-line paths relative to the cwd, matched against the index."""

from __future__ import annotations

import os
from pathlib import Path

from pygit.errors import GitError
from pygit.ignore import wildmatch

_GLOB_CHARS = set(b"*?[")


def cwd_prefix(repo) -> bytes:
    """The cwd relative to the work tree root, as b"" or b"dir/sub/"."""
    if repo.worktree is None:
        return b""
    cwd = Path.cwd().resolve()
    try:
        rel = cwd.relative_to(repo.worktree)
    except ValueError:
        return b""
    s = rel.as_posix()
    return b"" if s in ("", ".") else os.fsencode(s) + b"/"


def to_repo_path(repo, arg: str) -> bytes:
    """Normalize a command-line path to a repository-relative path."""
    prefix = cwd_prefix(repo)
    raw = arg.replace("\\", "/") if os.name == "nt" else arg
    p = Path(raw)
    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(repo.worktree).as_posix()
        except ValueError:
            raise GitError(f"{arg}: '{arg}' is outside repository at '{repo.worktree.as_posix()}'")
        rel = os.fsencode(rel)
        return b"" if rel == b"." else rel
    parts = []
    for part in (prefix + os.fsencode(raw)).split(b"/"):
        if part in (b"", b"."):
            continue
        if part == b"..":
            if not parts:
                raise GitError(f"{arg}: '{arg}' is outside repository at '{repo.worktree.as_posix()}'")
            parts.pop()
            continue
        parts.append(part)
    return b"/".join(parts)


def relative_to_cwd(path: bytes, prefix: bytes) -> bytes:
    """Show a repository path relative to the cwd, with ../ as needed."""
    if not prefix:
        return path
    slash = path.endswith(b"/")  # directories shown as "dir/"
    p_parts = prefix.rstrip(b"/").split(b"/")
    parts = path.rstrip(b"/").split(b"/")
    if p_parts[:len(parts)] == parts:
        # The path is the cwd or one of its parents: "./", "../", "../../"...
        up = len(p_parts) - len(parts)
        return b"../" * up if up else b"./"
    i = 0
    limit = len(parts) if slash else len(parts) - 1
    while i < len(p_parts) and i < limit and p_parts[i] == parts[i]:
        i += 1
    rel = b"../" * (len(p_parts) - i) + b"/".join(parts[i:])
    if slash:
        rel = (rel.rstrip(b"/") or b".") + b"/"
    return rel


class Pathspec:
    def __init__(self, repo, args: list[str]):
        self.items = [to_repo_path(repo, a) for a in args]
        self.args = list(args)

    def __bool__(self):
        return bool(self.items)

    @staticmethod
    def _item_matches(item: bytes, path: bytes) -> bool:
        if item == b"" or path == item or path.startswith(item + b"/"):
            return True
        if any(c in _GLOB_CHARS for c in item):
            return wildmatch(item, path, pathname=False)
        return False

    def matches(self, path: bytes) -> bool:
        return not self.items or any(self._item_matches(i, path) for i in self.items)

    def matching_item(self, path: bytes):
        for n, i in enumerate(self.items):
            if self._item_matches(i, path):
                return n
        return None

    def leads_to(self, dirpath: bytes) -> bool:
        """Could something under directory `dirpath` match?"""
        if not self.items:
            return True
        for i in self.items:
            if self._item_matches(i, dirpath) or i.startswith(dirpath + b"/") or any(c in _GLOB_CHARS for c in i):
                return True
        return False

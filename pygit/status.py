"""Computing status: HEAD vs index (staged) and index vs work tree (unstaged)."""

from __future__ import annotations

import os
import stat as statmod
from dataclasses import dataclass, field

from pygit import worktree
from pygit.ignore import IgnoreRules
from pygit.index import Index, walk_tree
from pygit.objects import Commit
from pygit.refs import Refs


def head_commit(repo) -> str | None:
    oid, _ = Refs(repo).resolve("HEAD")
    return oid


def head_tree_entries(repo) -> dict[bytes, tuple[int, str]]:
    oid = head_commit(repo)
    if oid is None:
        return {}
    tree = Commit.parse(repo.odb.read(oid)[1]).tree
    return {p: (m, o) for p, m, o in walk_tree(repo.odb, tree)}


def _kind(mode: int) -> str:
    if mode == 0o120000:
        return "link"
    if mode == 0o160000:
        return "gitlink"
    return "file"


@dataclass
class Change:
    status: str          # 'A', 'M', 'D', 'T', 'R', 'U'
    path: bytes
    orig: bytes | None = None   # for renames
    score: int = 100


@dataclass
class Status:
    staged: list[Change] = field(default_factory=list)
    unstaged: list[Change] = field(default_factory=list)
    unmerged: list[tuple[bytes, str]] = field(default_factory=list)   # (path, XY)
    untracked: list[bytes] = field(default_factory=list)
    ignored: list[bytes] = field(default_factory=list)


def staged_changes(head: dict, idx: Index, detect_renames: bool = True,
                   odb=None, rename_threshold: int = 50) -> list[Change]:
    stage0 = {e.path: (e.mode, e.oid) for e in idx.entries.values() if e.stage == 0}
    conflicted = {p for p, s in idx.entries if s}
    changes = []
    for path in sorted(set(head) | set(stage0)):
        if path in conflicted:
            continue
        h = head.get(path)
        i = stage0.get(path)
        if h is None:
            changes.append(Change("A", path))
        elif i is None:
            changes.append(Change("D", path))
        elif _kind(h[0]) != _kind(i[0]):
            changes.append(Change("T", path))
        elif h != i:
            changes.append(Change("M", path))
    if detect_renames:
        from pygit.diffcore import detect_renames as dr
        old = {c.path: head[c.path] for c in changes if c.status == "D"}
        new = {c.path: stage0[c.path] for c in changes if c.status == "A"}
        pairs = dr(odb, old, new, rename_threshold) if odb is not None else []
        if pairs:
            gone = set()
            for src, dst, score in pairs:
                gone.add(src)
                gone.add(dst)
            changes = [c for c in changes if c.path not in gone or c.status not in "AD"]
            changes += [Change("R", dst, src, score) for src, dst, score in pairs]
            changes.sort(key=lambda c: c.path)
    return changes


def unstaged_changes(repo, idx: Index) -> list[Change]:
    changes = []
    seen_conflict = set()
    for e in idx.sorted_entries():
        if e.stage:
            seen_conflict.add(e.path)
            continue
        if e.ext_flags & 0x4000:  # skip-worktree
            continue
        st = worktree.lstat(repo, e.path)
        if st is None or (statmod.S_ISDIR(st.st_mode) and e.mode != 0o160000):
            changes.append(Change("D", e.path))
            continue
        if e.mode == 0o160000:
            continue
        symlinks = repo.config.get_bool("core.symlinks", os.name != "nt")
        is_link = statmod.S_ISLNK(st.st_mode)
        if symlinks and (is_link != (e.mode == 0o120000)):
            changes.append(Change("T", e.path))
            continue
        if worktree.is_modified(repo, e, st):
            changes.append(Change("M", e.path))
    return changes


UNMERGED_CODES = {
    (True, False, False): "DD", (False, True, False): "AU", (False, False, True): "UA",
    (True, True, False): "UD", (True, False, True): "DU", (False, True, True): "AA",
    (True, True, True): "UU",
}


def unmerged(idx: Index) -> list[tuple[bytes, str]]:
    stages: dict[bytes, set] = {}
    for p, s in idx.entries:
        if s:
            stages.setdefault(p, set()).add(s)
    out = []
    for p in sorted(stages):
        s = stages[p]
        out.append((p, UNMERGED_CODES[(1 in s, 2 in s, 3 in s)]))
    return out


def compute(repo, untracked_mode: str = "normal", ignored: bool = False,
            detect_renames: bool = True) -> Status:
    idx = Index.read(repo)
    if worktree.refresh(repo, idx):
        try:
            idx.write()
        except Exception:
            pass  # read-only repo or index locked: status still works
    head = head_tree_entries(repo)
    st = Status()
    st.staged = staged_changes(head, idx, detect_renames, repo.odb)
    st.unstaged = unstaged_changes(repo, idx)
    st.unmerged = unmerged(idx)
    if untracked_mode != "no" or ignored:
        rules = IgnoreRules(repo)
        u, ig = worktree.untracked(repo, idx, rules, all_files=(untracked_mode == "all"),
                                   include_ignored=ignored)
        st.untracked = u if untracked_mode != "no" else []
        st.ignored = ig
    return st

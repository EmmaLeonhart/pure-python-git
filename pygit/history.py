"""Walking commit history: ancestors, ordering, merge bases, ahead/behind."""

from __future__ import annotations

import heapq

from pygit.objects import Commit


class CommitCache:
    """Parsed commits by id, so a walk reads each object once."""

    def __init__(self, repo):
        self.repo = repo
        self._c: dict[str, Commit] = {}

    def get(self, oid: str) -> Commit:
        c = self._c.get(oid)
        if c is None:
            t, data = self.repo.odb.read(oid)
            if t != b"commit":
                from pygit.errors import GitError
                raise GitError(f"object {oid} is a {t.decode()}, not a commit")
            c = Commit.parse(data)
            self._c[oid] = c
        return c


def walk(repo, include: list[str], exclude: list[str] = (), first_parent: bool = False,
         cache: CommitCache | None = None):
    """Yield commit ids reachable from `include` but not from `exclude`,
    newest committer date first (git's default `rev-list` order)."""
    cache = cache or CommitCache(repo)
    hidden = set(ancestors(repo, list(exclude), cache)) if exclude else set()
    seen = set()
    heap = []
    counter = 0
    for oid in include:
        if oid in seen or oid in hidden:
            continue
        seen.add(oid)
        heapq.heappush(heap, (-cache.get(oid).committer.timestamp, counter, oid))
        counter += 1
    while heap:
        _, _, oid = heapq.heappop(heap)
        yield oid
        parents = cache.get(oid).parents
        if first_parent:
            parents = parents[:1]
        for p in parents:
            if p in seen or p in hidden:
                continue
            seen.add(p)
            heapq.heappush(heap, (-cache.get(p).committer.timestamp, counter, p))
            counter += 1


def ancestors(repo, starts: list[str], cache: CommitCache | None = None) -> set[str]:
    """Every commit reachable from `starts`, inclusive."""
    cache = cache or CommitCache(repo)
    seen = set()
    stack = list(starts)
    while stack:
        oid = stack.pop()
        if oid in seen:
            continue
        seen.add(oid)
        stack.extend(cache.get(oid).parents)
    return seen


def merge_bases(repo, a: str, b: str, cache: CommitCache | None = None) -> list[str]:
    """Best common ancestors of a and b (those not reachable from another)."""
    cache = cache or CommitCache(repo)
    common = ancestors(repo, [a], cache) & ancestors(repo, [b], cache)
    if not common:
        return []
    # Drop any common ancestor that is reachable from another one.
    redundant = set()
    for c in common:
        if c in redundant:
            continue
        for p in ancestors(repo, cache.get(c).parents, cache):
            if p in common:
                redundant.add(p)
    best = [c for c in common if c not in redundant]
    best.sort(key=lambda c: (-cache.get(c).committer.timestamp, c))
    return best


def is_ancestor(repo, a: str, b: str, cache: CommitCache | None = None) -> bool:
    """Is a reachable from b?"""
    return a in ancestors(repo, [b], cache)


def ahead_behind(repo, local: str, upstream: str) -> tuple[int, int]:
    cache = CommitCache(repo)
    la = ancestors(repo, [local], cache)
    ua = ancestors(repo, [upstream], cache)
    return len(la - ua), len(ua - la)

"""Three-way tree merge with the results and messages of git's "ort" strategy
for the common cases: clean merges, content conflicts, add/add,
modify/delete, renames on either side (with content merged at the new
path), rename/delete, rename/rename, mode changes, binary files and
file/directory clashes.

The result is a list of index entries (stage 0 for resolved paths, stages
1-3 for conflicts), the bytes to write into the work tree for conflicted
paths, and the messages ort prints, sorted by path as ort sorts them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pygit import xdiff, xmerge
from pygit.diffcore import detect_renames
from pygit.index import walk_tree


@dataclass
class MergeResult:
    entries: dict = field(default_factory=dict)    # (path, stage) -> (mode, oid)
    worktree: dict = field(default_factory=dict)   # path -> (mode, bytes) for conflicted files
    messages: list = field(default_factory=list)   # (path, line)
    conflicted: list = field(default_factory=list) # paths, in order
    clean: bool = True

    def say(self, path: bytes, line: str) -> None:
        self.messages.append((path, line))

    def sorted_messages(self) -> list[str]:
        """By path (a stable sort keeps each path's messages in order)."""
        return [line for p, line in sorted(self.messages, key=lambda m: m[0])]

    def conflict(self, path: bytes) -> None:
        self.clean = False
        if path not in self.conflicted:
            self.conflicted.append(path)


def _tree(odb, oid):
    return {p: (m, o) for p, m, o in walk_tree(odb, oid)} if oid else {}


def _name(p: bytes) -> str:
    return p.decode("utf-8", "replace")


class Merger:
    def __init__(self, repo, label_ours: str, label_theirs: str, label_base: str | None,
                 style: int = xmerge.STYLE_MERGE, renames: bool = True):
        self.repo = repo
        self.odb = repo.odb
        self.l1, self.l2, self.l0 = label_ours, label_theirs, label_base
        self.style = style
        self.renames = renames

    # -- content ---------------------------------------------------------------------
    def merge_content(self, res: MergeResult, path: bytes, base, ours, theirs,
                      path_ours: bytes | None = None, path_theirs: bytes | None = None):
        """Merge three (mode, oid) versions (base may be None). Returns
        (mode, oid, clean). Writes conflict markers into a new blob when not clean."""
        mode, mode_clean = self._merge_mode(base, ours, theirs)
        if ours[1] == theirs[1]:
            return mode, ours[1], mode_clean
        if base is not None and base[1] == ours[1]:
            return mode, theirs[1], mode_clean
        if base is not None and base[1] == theirs[1]:
            return mode, ours[1], mode_clean
        res.say(path, f"Auto-merging {_name(path)}")
        b = self.odb.read(base[1])[1] if base is not None else b""
        o = self.odb.read(ours[1])[1]
        t = self.odb.read(theirs[1])[1]
        if ours[0] == 0o160000 or theirs[0] == 0o160000:
            res.say(path, f"CONFLICT (submodule): Merge conflict in {_name(path)}")
            return mode, ours[1], False
        if xdiff.is_binary(b) or xdiff.is_binary(o) or xdiff.is_binary(t):
            res.say(path, f"warning: Cannot merge binary files: {_name(path)} ({self.l1} vs. {self.l2})")
            return mode, ours[1], False
        l1 = self.l1 if path_ours is None or path_ours == path_theirs else f"{self.l1}:{_name(path_ours)}"
        l2 = self.l2 if path_theirs is None or path_ours == path_theirs else f"{self.l2}:{_name(path_theirs)}"
        merged, conflicts = xmerge.merge(b, o, t, l1, l2, self.l0, level=xmerge.ZEALOUS, style=self.style)
        oid = self.odb.write(b"blob", merged)
        return mode, oid, conflicts == 0 and mode_clean

    def _merge_mode(self, base, ours, theirs):
        if ours[0] == theirs[0]:
            return ours[0], True
        if base is not None and base[0] == ours[0]:
            return theirs[0], True
        if base is not None and base[0] == theirs[0]:
            return ours[0], True
        return ours[0], False

    # -- the tree merge -----------------------------------------------------------------
    def merge_trees(self, base_tree, ours_tree, theirs_tree) -> MergeResult:
        B, O, T = _tree(self.odb, base_tree), _tree(self.odb, ours_tree), _tree(self.odb, theirs_tree)
        res = MergeResult()
        ren_o = self._renames(B, O)
        ren_t = self._renames(B, T)
        handled = set()

        def put(path, value):
            res.entries[(path, 0)] = value

        def stages(path, b, o, t):
            for s, v in ((1, b), (2, o), (3, t)):
                if v is not None:
                    res.entries[(path, s)] = v

        # Renames first: the base path moved on at least one side.
        for src in sorted(set(ren_o) | set(ren_t)):
            b = B[src]
            dst_o, dst_t = ren_o.get(src), ren_t.get(src)
            handled.add(src)
            if dst_o and dst_t:
                handled.update((dst_o, dst_t))
                if dst_o == dst_t:
                    self._resolve(res, dst_o, b, O[dst_o], T[dst_t], put, stages)
                else:
                    res.say(src, f"CONFLICT (rename/rename): {_name(src)} renamed to {_name(dst_o)} in "
                                 f"{self.l1} and to {_name(dst_t)} in {self.l2}.")
                    res.conflict(dst_o)
                    res.conflict(dst_t)
                    res.entries[(dst_o, 1)] = b
                    res.entries[(dst_o, 2)] = O[dst_o]
                    res.entries[(dst_t, 1)] = b
                    res.entries[(dst_t, 3)] = T[dst_t]
                    res.worktree[dst_o] = (O[dst_o][0], self.odb.read(O[dst_o][1])[1])
                    res.worktree[dst_t] = (T[dst_t][0], self.odb.read(T[dst_t][1])[1])
                continue
            if dst_o:
                handled.add(dst_o)
                other = T.get(src)
                if other is None:
                    res.say(src, f"CONFLICT (rename/delete): {_name(src)} renamed to {_name(dst_o)} in "
                                 f"{self.l1}, but deleted in {self.l2}.")
                    res.conflict(dst_o)
                    res.entries[(dst_o, 1)] = b
                    res.entries[(dst_o, 2)] = O[dst_o]
                    res.worktree[dst_o] = (O[dst_o][0], self.odb.read(O[dst_o][1])[1])
                    continue
                if dst_o in T and dst_o not in B:
                    handled.add(dst_o)
                self._resolve(res, dst_o, b, O[dst_o], other, put, stages, src, dst_o, src)
            else:
                handled.add(dst_t)
                other = O.get(src)
                if other is None:
                    res.say(src, f"CONFLICT (rename/delete): {_name(src)} renamed to {_name(dst_t)} in "
                                 f"{self.l2}, but deleted in {self.l1}.")
                    res.conflict(dst_t)
                    res.entries[(dst_t, 1)] = b
                    res.entries[(dst_t, 3)] = T[dst_t]
                    res.worktree[dst_t] = (T[dst_t][0], self.odb.read(T[dst_t][1])[1])
                    continue
                self._resolve(res, dst_t, b, other, T[dst_t], put, stages, dst_t, src, dst_t)

        for path in sorted(set(B) | set(O) | set(T)):
            if path in handled:
                continue
            b, o, t = B.get(path), O.get(path), T.get(path)
            if o == t:
                if o is not None:
                    put(path, o)
                continue
            if b == o:
                if t is not None:
                    put(path, t)
                continue
            if b == t:
                if o is not None:
                    put(path, o)
                continue
            if o is None or t is None:
                # modify/delete
                deleted_in, modified_in = (self.l1, self.l2) if o is None else (self.l2, self.l1)
                kept = t if o is None else o
                res.say(path, f"CONFLICT (modify/delete): {_name(path)} deleted in {deleted_in} and "
                              f"modified in {modified_in}.  Version {modified_in} of {_name(path)} left in tree.")
                res.conflict(path)
                stages(path, b, o, t)
                res.worktree[path] = (kept[0], self.odb.read(kept[1])[1])
                continue
            self._resolve(res, path, b, o, t, put, stages)
        self._directory_clashes(res)
        return res

    def _resolve(self, res, path, b, o, t, put, stages, p_base=None, p_ours=None, p_theirs=None):
        if (o[0] & 0o170000) != (t[0] & 0o170000) and o[1] != t[1]:
            # Type change on one side (file vs symlink vs submodule).
            if b is not None and b == o:
                put(path, t)
                return
            if b is not None and b == t:
                put(path, o)
                return
            res.say(path, f"CONFLICT (distinct types): {_name(path)} had different types on each side; "
                          f"renamed both of them so each can be recorded somewhere.")
            res.conflict(path)
            stages(path, b, o, t)
            res.worktree[path] = (o[0], self.odb.read(o[1])[1])
            return
        mode, oid, clean = self.merge_content(res, path, b, o, t, p_ours, p_theirs)
        if clean:
            put(path, (mode, oid))
            return
        # ort reports content, binary and mode conflicts alike.
        reason = "add/add" if b is None else "content"
        res.say(path, f"CONFLICT ({reason}): Merge conflict in {_name(path)}")
        res.conflict(path)
        stages(path, b, o, t)
        res.worktree[path] = (mode, self.odb.read(oid)[1])

    def _renames(self, base: dict, side: dict) -> dict:
        if not self.renames:
            return {}
        deleted = {p: v for p, v in base.items() if p not in side}
        added = {p: v for p, v in side.items() if p not in base}
        if not deleted or not added:
            return {}
        return {src: dst for src, dst, _ in detect_renames(self.odb, deleted, added, 50)}

    def _directory_clashes(self, res: MergeResult) -> None:
        """A file where the other side has a directory moves aside to path~label."""
        paths = {p for p, s in res.entries}
        for p in sorted(paths):
            prefix = p + b"/"
            if not any(q.startswith(prefix) for q in paths):
                continue
            # p is a file and also a directory in the result.
            stages = {s: res.entries.pop((p, s)) for s in (0, 1, 2, 3) if (p, s) in res.entries}
            side = self.l1 if 2 in stages or 0 in stages else self.l2
            new = p + b"~" + side.encode()
            res.say(p, f"CONFLICT (file/directory): directory in the way of {_name(p)} from {side}; "
                       f"moving it to {_name(new)} instead.")
            res.conflict(new)
            for s, v in stages.items():
                res.entries[(new, s if s else (2 if side == self.l1 else 3))] = v
            if p in res.worktree:
                res.worktree[new] = res.worktree.pop(p)
            else:
                v = stages.get(0) or stages.get(2) or stages.get(3)
                res.worktree[new] = (v[0], self.odb.read(v[1])[1])


def virtual_base(repo, bases: list[str], merger_factory) -> str | None:
    """The tree to use as merge base. Several best bases are merged
    recursively into a virtual tree, keeping conflict markers in content."""
    from pygit.objects import Commit
    from pygit.history import merge_bases
    if not bases:
        return None
    trees = [Commit.parse(repo.odb.read(b)[1]).tree for b in bases]
    if len(bases) == 1:
        return trees[0]
    acc_commit, acc_tree = bases[0], trees[0]
    for nxt, nxt_tree in zip(bases[1:], trees[1:]):
        inner = merge_bases(repo, acc_commit, nxt) if acc_commit else []
        inner_tree = virtual_base(repo, inner, merger_factory) if inner else None
        m = merger_factory("Temporary merge branch 1", "Temporary merge branch 2")
        res = m.merge_trees(inner_tree, acc_tree, nxt_tree)
        acc_tree = tree_from_result(repo, res, take_conflicts=True)
        acc_commit = None
    return acc_tree


def tree_from_result(repo, res: MergeResult, take_conflicts: bool = False) -> str:
    """Write a tree: stage 0 entries, plus (for virtual bases) the work-tree
    version of conflicted paths."""
    from pygit.index import Index, IndexEntry
    idx = Index(repo.gitdir / "index.virtual")
    for (p, s), (m, o) in res.entries.items():
        if s == 0:
            idx.entries[(p, 0)] = IndexEntry(path=p, oid=o, mode=m)
    if take_conflicts:
        for p, (m, data) in res.worktree.items():
            oid = repo.odb.write(b"blob", data)
            idx.entries[(p, 0)] = IndexEntry(path=p, oid=oid, mode=m)
    return idx.write_tree(repo.odb)

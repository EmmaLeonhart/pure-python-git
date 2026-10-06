"""Differences between two snapshots (trees, the index, the work tree) as a
list of file pairs, with rename detection, plus the diffstat summary that
`git commit` prints."""

from __future__ import annotations

from dataclasses import dataclass

from pygit import xdiff
from pygit.diffcore import detect_renames
from pygit.index import walk_tree


@dataclass
class FilePair:
    status: str                # A, D, M, T, R
    old_path: bytes | None
    new_path: bytes | None
    old_mode: int = 0
    new_mode: int = 0
    old_oid: str | None = None
    new_oid: str | None = None
    score: int = 0             # similarity percent for renames

    @property
    def path(self) -> bytes:
        return self.new_path if self.new_path is not None else self.old_path


def _kind(mode: int) -> int:
    return mode & 0o170000


def tree_entries(odb, tree: str | None) -> dict[bytes, tuple[int, str]]:
    if not tree:
        return {}
    return {p: (m, o) for p, m, o in walk_tree(odb, tree)}


def diff_maps(odb, old: dict, new: dict, renames: bool = True, rename_threshold: int = 50,
              break_typechanges: bool = False) -> list[FilePair]:
    """Compare path -> (mode, oid) maps. Sorted by path, as git's diff queue."""
    pairs = []
    for path in sorted(set(old) | set(new)):
        o, n = old.get(path), new.get(path)
        if o == n:
            continue
        if o is None:
            pairs.append(FilePair("A", None, path, 0, n[0], None, n[1]))
        elif n is None:
            pairs.append(FilePair("D", path, None, o[0], 0, o[1], None))
        elif _kind(o[0]) != _kind(n[0]):
            if break_typechanges:
                pairs.append(FilePair("D", path, None, o[0], 0, o[1], None))
                pairs.append(FilePair("A", None, path, 0, n[0], None, n[1]))
            else:
                pairs.append(FilePair("T", path, path, o[0], n[0], o[1], n[1]))
        else:
            pairs.append(FilePair("M", path, path, o[0], n[0], o[1], n[1]))
    if renames:
        deleted = {p.old_path: (p.old_mode, p.old_oid) for p in pairs if p.status == "D"}
        added = {p.new_path: (p.new_mode, p.new_oid) for p in pairs if p.status == "A"}
        found = detect_renames(odb, deleted, added, rename_threshold) if deleted and added else []
        if found:
            used = {s for s, _, _ in found} | {d for _, d, _ in found}
            pairs = [p for p in pairs if not (p.status in "AD" and p.path in used)]
            for src, dst, score in found:
                om, oo = deleted[src]
                nm, no = added[dst]
                pairs.append(FilePair("R", src, dst, om, nm, oo, no, score))
            pairs.sort(key=lambda p: p.path)
    return pairs


def blob(odb, oid: str | None) -> bytes:
    return odb.read(oid)[1] if oid else b""


def line_counts(odb, pair: FilePair) -> tuple[int, int, bool]:
    """(insertions, deletions, is_binary) for one file pair."""
    if pair.old_mode == 0o160000 or pair.new_mode == 0o160000:
        return (1 if pair.new_oid else 0), (1 if pair.old_oid else 0), False
    a, b = blob(odb, pair.old_oid), blob(odb, pair.new_oid)
    if xdiff.is_binary(a) or xdiff.is_binary(b):
        return 0, 0, True
    if pair.old_oid == pair.new_oid:
        return 0, 0, False
    la, lb = xdiff.split_lines(a), xdiff.split_lines(b)
    ins, dels = xdiff.counts(xdiff.diff(la, lb))
    return ins, dels, False


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def shortstat(files: int, ins: int, dels: int) -> bytes:
    """` N files changed, X insertions(+), Y deletions(-)` (print_stat_summary)."""
    if not files:
        return b" 0 files changed\n"
    s = f" {files} {_plural(files, 'file changed', 'files changed')}"
    if ins or not dels:
        s += f", {ins} {_plural(ins, 'insertion(+)', 'insertions(+)')}"
    if dels or not ins:
        s += f", {dels} {_plural(dels, 'deletion(-)', 'deletions(-)')}"
    return (s + "\n").encode()


def pprint_rename(a: bytes, b: bytes) -> bytes:
    """`dir/{old => new}` with the common prefix and suffix factored out."""
    # Common prefix up to a '/'.
    pfx = 0
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        if a[i] == 0x2F:
            pfx = i + 1
        i += 1
    # Common suffix starting at a '/', not overlapping the prefix.
    sfx = 0
    ia, ib = len(a) - 1, len(b) - 1
    while ia >= pfx and ib >= pfx and a[ia] == b[ib]:
        if a[ia] == 0x2F:
            sfx = len(a) - ia
        ia -= 1
        ib -= 1
    a_mid = a[pfx:len(a) - sfx]
    b_mid = b[pfx:len(b) - sfx]
    if pfx or sfx:
        return a[:pfx] + b"{" + a_mid + b" => " + b_mid + b"}" + a[len(a) - sfx:]
    return a + b" => " + b


def summary_lines(pairs: list[FilePair]) -> bytes:
    """The ` create mode`/` delete mode`/` rename`/` mode change` lines."""
    from pygit.quote import quote_path
    out = []
    for p in pairs:
        if p.status == "A":
            out.append(b" create mode %06o " % p.new_mode + quote_path(p.new_path) + b"\n")
        elif p.status == "D":
            out.append(b" delete mode %06o " % p.old_mode + quote_path(p.old_path) + b"\n")
        elif p.status == "R":
            out.append(b" rename " + pprint_rename(p.old_path, p.new_path) + b" (%d%%)\n" % p.score)
            if p.old_mode != p.new_mode:
                out.append(b" mode change %06o => %06o\n" % (p.old_mode, p.new_mode))
        elif p.status in "MT" and p.old_mode != p.new_mode:
            out.append(b" mode change %06o => %06o " % (p.old_mode, p.new_mode) + quote_path(p.new_path) + b"\n")
    return b"".join(out)

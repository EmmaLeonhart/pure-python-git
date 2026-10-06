"""Three-way merge of file contents: a port of git's xdiff/xmerge.c.

Both sides are diffed against the base (without the indent heuristic, as
git's merge machinery does), the two edit scripts are walked together, and
overlapping changes become conflicts unless they are identical. At the
"zealous" levels conflicts are refined (common lines at their edges are
pulled out by diffing side 1 against side 2) and conflicts separated by
fewer than four lines are joined.
"""

from __future__ import annotations

from pygit import xdiff

MINIMAL, EAGER, ZEALOUS, ZEALOUS_ALNUM = 0, 1, 2, 3
STYLE_MERGE, STYLE_DIFF3, STYLE_ZEALOUS_DIFF3 = 0, 1, 2
FAVOR_NONE, FAVOR_OURS, FAVOR_THEIRS, FAVOR_UNION = 0, 1, 2, 3
DEFAULT_MARKER_SIZE = 7


class _M:
    """xdmerge_t. mode: 0 conflict, 1 take side 1, 2 take side 2, 3 both, 4 identical."""
    __slots__ = ("mode", "i0", "chg0", "i1", "chg1", "i2", "chg2")

    def __init__(self, mode, i0, chg0, i1, chg1, i2, chg2):
        self.mode, self.i0, self.chg0 = mode, i0, chg0
        self.i1, self.chg1, self.i2, self.chg2 = i1, chg1, i2, chg2


def _append(changes: list, mode, i0, chg0, i1, chg1, i2, chg2) -> None:
    m = changes[-1] if changes else None
    if m is not None and (i1 <= m.i1 + m.chg1 or i2 <= m.i2 + m.chg2):
        if mode != m.mode:
            m.mode = 0
        m.chg0 = i0 + chg0 - m.i0
        m.chg1 = i1 + chg1 - m.i1
        m.chg2 = i2 + chg2 - m.i2
    else:
        changes.append(_M(mode, i0, chg0, i1, chg1, i2, chg2))


def _isalnum(c: int) -> bool:
    return 0x30 <= c <= 0x39 or 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A


def _lines_contain_alnum(recs, i, chg) -> bool:
    return any(any(_isalnum(c) for c in recs[j]) for j in range(i, i + chg))


def _refine_conflicts(r1, r2, changes: list) -> list:
    out = []
    for m in changes:
        if m.mode or m.chg1 == 0 or m.chg2 == 0:
            out.append(m)
            continue
        t1 = r1[m.i1:m.i1 + m.chg1]
        t2 = r2[m.i2:m.i2 + m.chg2]
        script = xdiff.diff(t1, t2, indent_heuristic=False)
        if not script:
            m.mode = 4  # the changes are identical
            out.append(m)
            continue
        i1, i2 = m.i1, m.i2
        for k, x in enumerate(script):
            n = m if k == 0 else _M(0, 0, 0, 0, 0, 0, 0)
            n.i1, n.chg1 = x.i1 + i1, x.chg1
            n.i2, n.chg2 = x.i2 + i2, x.chg2
            out.append(n)
    return out


def _simplify_non_conflicts(r1, changes: list, simplify_if_no_alnum: bool) -> list:
    if not changes:
        return changes
    out = [changes[0]]
    for nxt in changes[1:]:
        m = out[-1]
        begin = m.i1 + m.chg1
        end = nxt.i1
        if m.mode != 0 or nxt.mode != 0 or (
                end - begin > 3 and (not simplify_if_no_alnum or _lines_contain_alnum(r1, begin, end - begin))):
            out.append(nxt)
        else:
            m.chg1 = nxt.i1 + nxt.chg1 - m.i1
            m.chg2 = nxt.i2 + nxt.chg2 - m.i2
    return out


def _copy(recs, i, count, add_nl) -> bytes:
    if count < 1:
        return b""
    data = b"".join(recs[i:i + count])
    if add_nl and not data.endswith(b"\n"):
        data += b"\n"
    return data


def merge(base: bytes, ours: bytes, theirs: bytes, name1: str = "ours", name2: str = "theirs",
          ancestor_name: str | None = None, level: int = ZEALOUS, style: int = STYLE_MERGE,
          favor: int = FAVOR_NONE, marker_size: int = DEFAULT_MARKER_SIZE) -> tuple[bytes, int]:
    """Returns (merged bytes, number of conflicts)."""
    r0 = xdiff.split_lines(base)
    r1 = xdiff.split_lines(ours)
    r2 = xdiff.split_lines(theirs)
    s1 = xdiff.diff(r0, r1, indent_heuristic=False)
    s2 = xdiff.diff(r0, r2, indent_heuristic=False)
    if not s1:
        return theirs, 0
    if not s2:
        return ours, 0
    if style == STYLE_DIFF3 and level > EAGER:
        level = EAGER
    changes: list[_M] = []
    a, b = 0, 0
    while a < len(s1) and b < len(s2):
        x1, x2 = s1[a], s2[b]
        if x1.i1 + x1.chg1 < x2.i1:
            _append(changes, 1, x1.i1, x1.chg1, x1.i2, x1.chg2, x2.i2 - x2.i1 + x1.i1, x1.chg1)
            a += 1
            continue
        if x2.i1 + x2.chg1 < x1.i1:
            _append(changes, 2, x2.i1, x2.chg1, x1.i2 - x1.i1 + x2.i1, x2.chg1, x2.i2, x2.chg2)
            b += 1
            continue
        if level == MINIMAL or x1.i1 != x2.i1 or x1.chg1 != x2.chg1 or x1.chg2 != x2.chg2 or \
                r1[x1.i2:x1.i2 + x1.chg2] != r2[x2.i2:x2.i2 + x1.chg2]:
            off = x1.i1 - x2.i1
            ffo = off + x1.chg1 - x2.chg1
            i0, i1, i2 = x1.i1, x1.i2, x2.i2
            if off > 0:
                i0 -= off
                i1 -= off
            else:
                i2 += off
            chg0 = x1.i1 + x1.chg1 - i0
            chg1 = x1.i2 + x1.chg2 - i1
            chg2 = x2.i2 + x2.chg2 - i2
            if ffo < 0:
                chg0 -= ffo
                chg1 -= ffo
            else:
                chg2 += ffo
            _append(changes, 0, i0, chg0, i1, chg1, i2, chg2)
        e1 = x1.i1 + x1.chg1
        e2 = x2.i1 + x2.chg1
        if e1 >= e2:
            b += 1
        if e2 >= e1:
            a += 1
    n0, n1, n2 = len(r0), len(r1), len(r2)
    while a < len(s1):
        x1 = s1[a]
        _append(changes, 1, x1.i1, x1.chg1, x1.i2, x1.chg2, x1.i1 + n2 - n0, x1.chg1)
        a += 1
    while b < len(s2):
        x2 = s2[b]
        _append(changes, 2, x2.i1, x2.chg1, x2.i1 + n1 - n0, x2.chg1, x2.i2, x2.chg2)
        b += 1
    if level >= ZEALOUS:
        changes = _refine_conflicts(r1, r2, changes)
        changes = _simplify_non_conflicts(r1, changes, level > ZEALOUS)
    # Output (xdl_fill_merge_buffer).
    out = []
    i = 0
    conflicts = 0
    for m in changes:
        if favor and not m.mode:
            m.mode = favor
        if m.mode == 0:
            conflicts += 1
            out.append(_copy(r1, i, m.i1 - i, False))
            out.append(b"<" * marker_size + ((b" " + name1.encode()) if name1 else b"") + b"\n")
            out.append(_copy(r1, m.i1, m.chg1, True))
            if style in (STYLE_DIFF3, STYLE_ZEALOUS_DIFF3):
                out.append(b"|" * marker_size + ((b" " + ancestor_name.encode()) if ancestor_name else b"") + b"\n")
                out.append(_copy(r0, m.i0, m.chg0, True))
            out.append(b"=" * marker_size + b"\n")
            out.append(_copy(r2, m.i2, m.chg2, True))
            out.append(b">" * marker_size + ((b" " + name2.encode()) if name2 else b"") + b"\n")
        elif m.mode & 3:
            out.append(_copy(r1, i, m.i1 - i, False))
            if m.mode & 1:
                out.append(_copy(r1, m.i1, m.chg1, bool(m.mode & 2)))
            if m.mode & 2:
                out.append(_copy(r2, m.i2, m.chg2, False))
        else:
            continue
        i = m.i1 + m.chg1
    out.append(_copy(r1, i, n1 - i, False))
    return b"".join(out), conflicts

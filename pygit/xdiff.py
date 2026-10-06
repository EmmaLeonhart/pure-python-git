"""A port of git's xdiff: the Myers diff that `git diff` uses, with git's
preprocessing, heuristics and change compaction, so hunks come out the same.

Sources followed (git's xdiff/): xprepare.c (xdl_trim_ends,
xdl_cleanup_records, xdl_clean_mmatch), xdiffi.c (xdl_split, xdl_recs_cmp,
xdl_change_compact with the indent heuristic, xdl_build_script) and
xemit.c (hunk grouping, function-name context, record emission).

Lines are records that keep their trailing newline, so "a" and "a\\n" are
different lines, as in git.
"""

from __future__ import annotations

from dataclasses import dataclass

XDL_MAX_COST_MIN = 256
XDL_HEUR_MIN_COST = 256
XDL_SNAKE_CNT = 20
XDL_K_HEUR = 4
XDL_MAX_EQLIMIT = 1024
XDL_SIMSCAN_WINDOW = 100
XDL_KPDIS_RUN = 4
XDL_LINE_MAX = 1 << 62

MAX_INDENT = 200
MAX_BLANKS = 20
START_OF_FILE_PENALTY = 1
END_OF_FILE_PENALTY = 21
TOTAL_BLANK_WEIGHT = -30
POST_BLANK_WEIGHT = 6
RELATIVE_INDENT_PENALTY = -4
RELATIVE_INDENT_WITH_BLANK_PENALTY = 10
RELATIVE_OUTDENT_PENALTY = 24
RELATIVE_OUTDENT_WITH_BLANK_PENALTY = 17
RELATIVE_DEDENT_PENALTY = 23
RELATIVE_DEDENT_WITH_BLANK_PENALTY = 17
INDENT_WEIGHT = 60
INDENT_HEURISTIC_MAX_SLIDING = 100


def split_lines(data: bytes) -> list[bytes]:
    """Records as xdiff sees them: each line with its '\\n'."""
    if not data:
        return []
    lines = data.split(b"\n")
    out = [l + b"\n" for l in lines[:-1]]
    if lines[-1]:
        out.append(lines[-1])
    return out


def bogosqrt(n: int) -> int:
    i = 1
    while n > 0:
        i <<= 1
        n >>= 2
    return i


class _File:
    """One side of the diff (xdfile_t)."""

    def __init__(self, recs: list[bytes], classes: list[int]):
        self.recs = recs
        self.nrec = len(recs)
        self.cls = classes          # equivalence class per record (rec->ha)
        # rchg has a sentinel slot on both sides: index i is stored at i + 1.
        self._rchg = bytearray(self.nrec + 2)
        self.rindex: list[int] = []
        self.ha: list[int] = []
        self.dstart = 0
        self.dend = self.nrec - 1

    def rchg(self, i: int) -> int:
        return self._rchg[i + 1]

    def set_rchg(self, i: int, v: int) -> None:
        self._rchg[i + 1] = v


def _prepare(a: list[bytes], b: list[bytes]):
    # Equivalence classes of identical lines, with per-file counts (len1/len2).
    classes: dict[bytes, int] = {}
    len1: list[int] = []
    len2: list[int] = []
    ca = []
    for r in a:
        c = classes.get(r)
        if c is None:
            c = len(classes)
            classes[r] = c
            len1.append(0)
            len2.append(0)
        len1[c] += 1
        ca.append(c)
    cb = []
    for r in b:
        c = classes.get(r)
        if c is None:
            c = len(classes)
            classes[r] = c
            len1.append(0)
            len2.append(0)
        len2[c] += 1
        cb.append(c)
    x1, x2 = _File(a, ca), _File(b, cb)
    _trim_ends(x1, x2)
    _cleanup_records(x1, x2, len1, len2)
    return x1, x2


def _trim_ends(x1: _File, x2: _File) -> None:
    lim = min(x1.nrec, x2.nrec)
    i = 0
    while i < lim and x1.cls[i] == x2.cls[i]:
        i += 1
    x1.dstart = x2.dstart = i
    lim -= i
    j = 0
    while j < lim and x1.cls[x1.nrec - 1 - j] == x2.cls[x2.nrec - 1 - j]:
        j += 1
    x1.dend = x1.nrec - j - 1
    x2.dend = x2.nrec - j - 1


def _clean_mmatch(dis, i: int, s: int, e: int) -> bool:
    if i - s > XDL_SIMSCAN_WINDOW:
        s = i - XDL_SIMSCAN_WINDOW
    if e - i > XDL_SIMSCAN_WINDOW:
        e = i + XDL_SIMSCAN_WINDOW
    rdis0, rpdis0 = 0, 1
    r = 1
    while i - r >= s:
        d = dis[i - r]
        if not d:
            rdis0 += 1
        elif d == 2:
            rpdis0 += 1
        else:
            break
        r += 1
    if rdis0 == 0:
        return False
    rdis1, rpdis1 = 0, 1
    r = 1
    while i + r <= e:
        d = dis[i + r]
        if not d:
            rdis1 += 1
        elif d == 2:
            rpdis1 += 1
        else:
            break
        r += 1
    if rdis1 == 0:
        return False
    rdis1 += rdis0
    rpdis1 += rpdis0
    return rpdis1 * XDL_KPDIS_RUN < rpdis1 + rdis1


def _cleanup_records(x1: _File, x2: _File, len1: list[int], len2: list[int]) -> None:
    dis1 = bytearray(x1.nrec + 1)
    dis2 = bytearray(x2.nrec + 1)
    mlim = min(bogosqrt(x1.nrec), XDL_MAX_EQLIMIT)
    for i in range(x1.dstart, x1.dend + 1):
        nm = len2[x1.cls[i]]
        dis1[i] = 0 if nm == 0 else (2 if nm >= mlim else 1)
    mlim = min(bogosqrt(x2.nrec), XDL_MAX_EQLIMIT)
    for i in range(x2.dstart, x2.dend + 1):
        nm = len1[x2.cls[i]]
        dis2[i] = 0 if nm == 0 else (2 if nm >= mlim else 1)
    for x, dis in ((x1, dis1), (x2, dis2)):
        for i in range(x.dstart, x.dend + 1):
            if dis[i] == 1 or (dis[i] == 2 and not _clean_mmatch(dis, i, x.dstart, x.dend)):
                x.rindex.append(i)
                x.ha.append(x.cls[i])
            else:
                x.set_rchg(i, 1)


def _split(ha1, off1, lim1, ha2, off2, lim2, kvdf, kvdb, need_min, env):
    """xdl_split: find the middle snake. Returns (i1, i2, min_lo, min_hi)."""
    dmin, dmax = off1 - lim2, lim1 - off2
    fmid, bmid = off1 - off2, lim1 - lim2
    odd = (fmid - bmid) & 1
    fmin = fmax = fmid
    bmin = bmax = bmid
    kvdf[fmid] = off1
    kvdb[bmid] = lim1
    mxcost, snake_cnt, heur_min = env
    ec = 0
    while True:
        ec += 1
        got_snake = False
        # forward
        if fmin > dmin:
            fmin -= 1
            kvdf[fmin - 1] = -1
        else:
            fmin += 1
        if fmax < dmax:
            fmax += 1
            kvdf[fmax + 1] = -1
        else:
            fmax -= 1
        for d in range(fmax, fmin - 1, -2):
            if kvdf[d - 1] >= kvdf[d + 1]:
                i1 = kvdf[d - 1] + 1
            else:
                i1 = kvdf[d + 1]
            prev1 = i1
            i2 = i1 - d
            while i1 < lim1 and i2 < lim2 and ha1[i1] == ha2[i2]:
                i1 += 1
                i2 += 1
            if i1 - prev1 > snake_cnt:
                got_snake = True
            kvdf[d] = i1
            if odd and bmin <= d <= bmax and kvdb[d] <= i1:
                return i1, i2, True, True
        # backward
        if bmin > dmin:
            bmin -= 1
            kvdb[bmin - 1] = XDL_LINE_MAX
        else:
            bmin += 1
        if bmax < dmax:
            bmax += 1
            kvdb[bmax + 1] = XDL_LINE_MAX
        else:
            bmax -= 1
        for d in range(bmax, bmin - 1, -2):
            if kvdb[d - 1] < kvdb[d + 1]:
                i1 = kvdb[d - 1]
            else:
                i1 = kvdb[d + 1] - 1
            prev1 = i1
            i2 = i1 - d
            while i1 > off1 and i2 > off2 and ha1[i1 - 1] == ha2[i2 - 1]:
                i1 -= 1
                i2 -= 1
            if prev1 - i1 > snake_cnt:
                got_snake = True
            kvdb[d] = i1
            if not odd and fmin <= d <= fmax and i1 <= kvdf[d]:
                return i1, i2, True, True
        if need_min:
            continue
        # heuristics
        if got_snake and ec > heur_min:
            best = 0
            spl = None
            for d in range(fmax, fmin - 1, -2):
                dd = d - fmid if d > fmid else fmid - d
                i1 = kvdf[d]
                i2 = i1 - d
                v = (i1 - off1) + (i2 - off2) - dd
                if v > XDL_K_HEUR * ec and v > best and off1 + snake_cnt <= i1 < lim1 \
                        and off2 + snake_cnt <= i2 < lim2:
                    k = 1
                    while ha1[i1 - k] == ha2[i2 - k]:
                        if k == snake_cnt:
                            best = v
                            spl = (i1, i2)
                            break
                        k += 1
            if best > 0:
                return spl[0], spl[1], True, False
            best = 0
            for d in range(bmax, bmin - 1, -2):
                dd = d - bmid if d > bmid else bmid - d
                i1 = kvdb[d]
                i2 = i1 - d
                v = (lim1 - i1) + (lim2 - i2) - dd
                if v > XDL_K_HEUR * ec and v > best and off1 < i1 <= lim1 - snake_cnt \
                        and off2 < i2 <= lim2 - snake_cnt:
                    k = 0
                    while ha1[i1 + k] == ha2[i2 + k]:
                        if k == snake_cnt - 1:
                            best = v
                            spl = (i1, i2)
                            break
                        k += 1
            if best > 0:
                return spl[0], spl[1], False, True
        # mxcost
        if ec >= mxcost:
            fbest = fbest1 = -1
            for d in range(fmax, fmin - 1, -2):
                i1 = min(kvdf[d], lim1)
                i2 = i1 - d
                if lim2 < i2:
                    i1 = lim2 + d
                    i2 = lim2
                if fbest < i1 + i2:
                    fbest = i1 + i2
                    fbest1 = i1
            bbest = bbest1 = XDL_LINE_MAX
            for d in range(bmax, bmin - 1, -2):
                i1 = max(off1, kvdb[d])
                i2 = i1 - d
                if i2 < off2:
                    i1 = off2 + d
                    i2 = off2
                if i1 + i2 < bbest:
                    bbest = i1 + i2
                    bbest1 = i1
            if (lim1 + lim2) - bbest < fbest - (off1 + off2):
                return fbest1, fbest - fbest1, True, False
            return bbest1, bbest - bbest1, False, True


class _KV:
    """A list indexed by possibly-negative diagonal numbers."""
    __slots__ = ("data", "off")

    def __init__(self, size: int, off: int):
        self.data = [0] * size
        self.off = off

    def __getitem__(self, k):
        return self.data[k + self.off]

    def __setitem__(self, k, v):
        self.data[k + self.off] = v


def _recs_cmp(x1: _File, off1, lim1, x2: _File, off2, lim2, kvdf, kvdb, need_min, env):
    ha1, ha2 = x1.ha, x2.ha
    stack = [(off1, lim1, off2, lim2, need_min)]
    while stack:
        off1, lim1, off2, lim2, need_min = stack.pop()
        while off1 < lim1 and off2 < lim2 and ha1[off1] == ha2[off2]:
            off1 += 1
            off2 += 1
        while off1 < lim1 and off2 < lim2 and ha1[lim1 - 1] == ha2[lim2 - 1]:
            lim1 -= 1
            lim2 -= 1
        if off1 == lim1:
            for i in range(off2, lim2):
                x2.set_rchg(x2.rindex[i], 1)
        elif off2 == lim2:
            for i in range(off1, lim1):
                x1.set_rchg(x1.rindex[i], 1)
        else:
            i1, i2, min_lo, min_hi = _split(ha1, off1, lim1, ha2, off2, lim2, kvdf, kvdb, need_min, env)
            # Same order as the recursion: low half first.
            stack.append((i1, lim1, i2, lim2, min_hi))
            stack.append((off1, i1, off2, i2, min_lo))


# -- compaction ------------------------------------------------------------------

def _get_indent(rec: bytes) -> int:
    ret = 0
    for c in rec:
        if c not in b" \t\n\r\x0b\x0c":
            return ret
        if c == 0x20:
            ret += 1
        elif c == 0x09:
            ret += 8 - ret % 8
        if ret >= MAX_INDENT:
            return MAX_INDENT
    return -1


def _measure_split(x: _File, split: int, indents):
    if split >= x.nrec:
        end_of_file, indent = True, -1
    else:
        end_of_file, indent = False, indents(split)
    pre_blank, pre_indent = 0, -1
    i = split - 1
    while i >= 0:
        pre_indent = indents(i)
        if pre_indent != -1:
            break
        pre_blank += 1
        if pre_blank == MAX_BLANKS:
            pre_indent = 0
            break
        i -= 1
    post_blank, post_indent = 0, -1
    i = split + 1
    while i < x.nrec:
        post_indent = indents(i)
        if post_indent != -1:
            break
        post_blank += 1
        if post_blank == MAX_BLANKS:
            post_indent = 0
            break
        i += 1
    return end_of_file, indent, pre_blank, pre_indent, post_blank, post_indent


def _score_add_split(m, s):
    end_of_file, m_indent, pre_blank, pre_indent, m_post_blank, post_indent = m
    eff, pen = s
    if pre_indent == -1 and pre_blank == 0:
        pen += START_OF_FILE_PENALTY
    if end_of_file:
        pen += END_OF_FILE_PENALTY
    post_blank = 1 + m_post_blank if m_indent == -1 else 0
    total_blank = pre_blank + post_blank
    pen += TOTAL_BLANK_WEIGHT * total_blank
    pen += POST_BLANK_WEIGHT * post_blank
    indent = m_indent if m_indent != -1 else post_indent
    any_blanks = total_blank != 0
    eff += indent
    if indent == -1 or pre_indent == -1:
        pass
    elif indent > pre_indent:
        pen += RELATIVE_INDENT_WITH_BLANK_PENALTY if any_blanks else RELATIVE_INDENT_PENALTY
    elif indent == pre_indent:
        pass
    else:
        if post_indent != -1 and post_indent > indent:
            pen += RELATIVE_OUTDENT_WITH_BLANK_PENALTY if any_blanks else RELATIVE_OUTDENT_PENALTY
        else:
            pen += RELATIVE_DEDENT_WITH_BLANK_PENALTY if any_blanks else RELATIVE_DEDENT_PENALTY
    return eff, pen


def _score_cmp(s1, s2) -> int:
    cmp_indents = (s1[0] > s2[0]) - (s1[0] < s2[0])
    return INDENT_WEIGHT * cmp_indents + (s1[1] - s2[1])


class _Group:
    __slots__ = ("start", "end")

    def __init__(self, x: _File):
        self.start = self.end = 0
        while x.rchg(self.end):
            self.end += 1


def _group_next(x, g) -> bool:
    if g.end == x.nrec:
        return False
    g.start = g.end + 1
    g.end = g.start
    while x.rchg(g.end):
        g.end += 1
    return True


def _group_previous(x, g) -> bool:
    if g.start == 0:
        return False
    g.end = g.start - 1
    g.start = g.end
    while x.rchg(g.start - 1):
        g.start -= 1
    return True


def _slide_down(x, g) -> bool:
    if g.end < x.nrec and x.cls[g.start] == x.cls[g.end]:
        x.set_rchg(g.start, 0)
        g.start += 1
        x.set_rchg(g.end, 1)
        g.end += 1
        while x.rchg(g.end):
            g.end += 1
        return True
    return False


def _slide_up(x, g) -> bool:
    if g.start > 0 and x.cls[g.start - 1] == x.cls[g.end - 1]:
        g.start -= 1
        x.set_rchg(g.start, 1)
        g.end -= 1
        x.set_rchg(g.end, 0)
        while x.rchg(g.start - 1):
            g.start -= 1
        return True
    return False


class DiffBug(Exception):
    pass


def _change_compact(x: _File, xo: _File, indent_heuristic: bool) -> None:
    cache = {}

    def indents(i):
        v = cache.get(i)
        if v is None:
            v = cache[i] = _get_indent(x.recs[i])
        return v

    g, go = _Group(x), _Group(xo)
    while True:
        if g.end != g.start:
            while True:
                groupsize = g.end - g.start
                end_matching_other = -1
                while _slide_up(x, g):
                    if not _group_previous(xo, go):
                        raise DiffBug("group sync broken sliding up")
                earliest_end = g.end
                if go.end > go.start:
                    end_matching_other = g.end
                while True:
                    if not _slide_down(x, g):
                        break
                    if not _group_next(xo, go):
                        raise DiffBug("group sync broken sliding down")
                    if go.end > go.start:
                        end_matching_other = g.end
                if groupsize == g.end - g.start:
                    break
            if g.end == earliest_end:
                pass
            elif end_matching_other != -1:
                while go.end == go.start:
                    if not _slide_up(x, g):
                        raise DiffBug("match disappeared")
                    if not _group_previous(xo, go):
                        raise DiffBug("group sync broken sliding to match")
            elif indent_heuristic:
                best_shift = -1
                best_score = None
                shift = earliest_end
                if g.end - groupsize - 1 > shift:
                    shift = g.end - groupsize - 1
                if g.end - INDENT_HEURISTIC_MAX_SLIDING > shift:
                    shift = g.end - INDENT_HEURISTIC_MAX_SLIDING
                while shift <= g.end:
                    score = (0, 0)
                    score = _score_add_split(_measure_split(x, shift, indents), score)
                    score = _score_add_split(_measure_split(x, shift - groupsize, indents), score)
                    if best_shift == -1 or _score_cmp(score, best_score) <= 0:
                        best_score = score
                        best_shift = shift
                    shift += 1
                while g.end > best_shift:
                    if not _slide_up(x, g):
                        raise DiffBug("best shift unreached")
                    if not _group_previous(xo, go):
                        raise DiffBug("group sync broken sliding to blank line")
        if not _group_next(x, g):
            break
        if not _group_next(xo, go):
            raise DiffBug("group sync broken moving to next group")


# -- public API ------------------------------------------------------------------

@dataclass
class Change:
    i1: int     # start in a (0-based)
    chg1: int   # lines removed
    i2: int     # start in b
    chg2: int   # lines added


def diff(a: list[bytes], b: list[bytes], minimal: bool = False,
         indent_heuristic: bool = True) -> list[Change]:
    """The edit script between two lists of records, as git computes it."""
    x1, x2 = _prepare(a, b)
    n1, n2 = len(x1.ha), len(x2.ha)
    ndiags = n1 + n2 + 3
    size = 2 * ndiags + 2
    kvdf = _KV(size, n2 + 1)
    kvdb = _KV(size, n2 + 1 + ndiags)
    mxcost = max(bogosqrt(ndiags), XDL_MAX_COST_MIN)
    _recs_cmp(x1, 0, n1, x2, 0, n2, kvdf, kvdb, minimal, (mxcost, XDL_SNAKE_CNT, XDL_HEUR_MIN_COST))
    _change_compact(x1, x2, indent_heuristic)
    _change_compact(x2, x1, indent_heuristic)
    # xdl_build_script
    changes = []
    i1, i2 = x1.nrec, x2.nrec
    while i1 >= 0 or i2 >= 0:
        if x1.rchg(i1 - 1) or x2.rchg(i2 - 1):
            l1, l2 = i1, i2
            while x1.rchg(i1 - 1):
                i1 -= 1
            while x2.rchg(i2 - 1):
                i2 -= 1
            changes.append(Change(i1, l1 - i1, i2, l2 - i2))
        i1 -= 1
        i2 -= 1
    changes.reverse()
    return changes


def _def_ff(rec: bytes) -> bytes | None:
    """git's default function-name test: a line starting with a letter, _ or $."""
    if rec and (chr(rec[0]).isalpha() and rec[0] < 128 or rec[0] in b"_$"):
        line = rec[:80]
        return line.rstrip(b" \t\n\r\x0b\x0c")
    return None


def unified(a: list[bytes], b: list[bytes], changes: list[Change], context: int = 3,
            interhunk: int = 0, funcnames: bool = True, find_func=None) -> bytes:
    """Hunks (`@@ ... @@` and lines) as xdl_emit_diff writes them."""
    out = []
    ff = find_func or _def_ff
    max_common = 2 * context + interhunk
    func_line = b""
    funclineprev = -1
    k = 0
    n = len(changes)
    while k < n:
        start = k
        while k + 1 < n and changes[k + 1].i1 - (changes[k].i1 + changes[k].chg1) <= max_common:
            k += 1
        first, last = changes[start], changes[k]
        k += 1
        s1 = max(first.i1 - context, 0)
        s2 = max(first.i2 - context, 0)
        e1 = min(last.i1 + last.chg1 + context, len(a))
        e2 = min(last.i2 + last.chg2 + context, len(b))
        if funcnames:
            limit = funclineprev
            l = s1 - 1
            while l != limit and 0 <= l < len(a):
                f = ff(a[l])
                if f is not None:
                    func_line = f
                    break
                l -= 1
            funclineprev = s1 - 1
        c1, c2 = e1 - s1, e2 - s2
        hdr = b"@@ -%d" % (s1 + 1 if c1 else s1)
        if c1 != 1:
            hdr += b",%d" % c1
        hdr += b" +%d" % (s2 + 1 if c2 else s2)
        if c2 != 1:
            hdr += b",%d" % c2
        hdr += b" @@"
        if funcnames and func_line:
            hdr += b" " + func_line
        out.append(hdr + b"\n")
        for j in range(s2, first.i2):
            out.append(_record(b" ", b[j]))
        p1, p2 = first.i1, first.i2
        for ch in changes[start:k]:
            while p1 < ch.i1 and p2 < ch.i2:
                out.append(_record(b" ", b[p2]))
                p1 += 1
                p2 += 1
            for j in range(ch.i1, ch.i1 + ch.chg1):
                out.append(_record(b"-", a[j]))
            for j in range(ch.i2, ch.i2 + ch.chg2):
                out.append(_record(b"+", b[j]))
            p1, p2 = ch.i1 + ch.chg1, ch.i2 + ch.chg2
        for j in range(last.i2 + last.chg2, e2):
            out.append(_record(b" ", b[j]))
    return b"".join(out)


def _record(prefix: bytes, rec: bytes) -> bytes:
    if rec.endswith(b"\n"):
        return prefix + rec
    return prefix + rec + b"\n\\ No newline at end of file\n"


def counts(changes: list[Change]) -> tuple[int, int]:
    """(insertions, deletions)."""
    return sum(c.chg2 for c in changes), sum(c.chg1 for c in changes)


def is_binary(data: bytes) -> bool:
    """git's buffer_is_binary: a NUL in the first 8000 bytes."""
    return b"\0" in data[:8000]

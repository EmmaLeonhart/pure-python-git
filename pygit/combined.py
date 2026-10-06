"""Combined diffs (`diff --cc`): a port of git's combine-diff.c.

The result (a merge commit's file, or the work-tree file during a
conflicted merge) is diffed against each parent with no context. Every
result line records which parents lack it (its '+' columns); lines a
parent had but the result lost hang in front of the result line that
follows them, with a mask of the parents that had them (the '-' columns),
lost lines of later parents being merged into earlier ones by LCS
(coalesce_lines). In dense mode (`--cc`) a hunk is dropped when the
result there matches one of the parents.
"""

from __future__ import annotations

from pygit import xdiff

CONTEXT = 3


class _SLine:
    __slots__ = ("bol", "flag", "lost", "plost", "p_lno")

    def __init__(self, bol: bytes | None, num_parent: int):
        self.bol = bol
        self.flag = 0
        self.lost: list[list] = []    # [line, parent_map]
        self.plost: list[bytes] = []  # lost lines of the parent being added
        self.p_lno = [0] * num_parent


def _coalesce(base: list, new: list[bytes], parent: int) -> list:
    """coalesce_lines(): merge `new` lost lines into `base` by LCS."""
    bit = 1 << parent
    if not new:
        return base
    if not base:
        return [[line, bit] for line in new]
    nb, nn = len(base), len(new)
    lcs = [[0] * (nn + 1) for _ in range(nb + 1)]
    dirs = [[0] * (nn + 1) for _ in range(nb + 1)]  # 0 BASE, 1 NEW, 2 MATCH
    for j in range(1, nn + 1):
        dirs[0][j] = 1
    for i in range(1, nb + 1):
        for j in range(1, nn + 1):
            if base[i - 1][0] == new[j - 1]:
                lcs[i][j] = lcs[i - 1][j - 1] + 1
                dirs[i][j] = 2
            elif lcs[i][j - 1] >= lcs[i - 1][j]:
                lcs[i][j] = lcs[i][j - 1]
                dirs[i][j] = 1
            else:
                lcs[i][j] = lcs[i - 1][j]
                dirs[i][j] = 0
    out = []
    i, j = nb, nn
    while i or j:
        d = dirs[i][j]
        if d == 2:
            base[i - 1][1] |= bit
            out.append(base[i - 1])
            i -= 1
            j -= 1
        elif d == 1:
            out.append([new[j - 1], bit])
            j -= 1
        else:
            out.append(base[i - 1])
            i -= 1
    out.reverse()
    return out


def _split_result(data: bytes) -> list[bytes]:
    if not data:
        return []
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    return lines


def _add_parent(slines, cnt, result_recs, parent_data: bytes, n: int, num_parent: int) -> None:
    mask = 1 << n
    precs = xdiff.split_lines(parent_data)
    for ch in xdiff.diff(precs, result_recs):
        bucket = slines[ch.i2]
        for k in range(ch.i1, ch.i1 + ch.chg1):
            line = precs[k]
            bucket.plost.append(line[:-1] if line.endswith(b"\n") else line)
        for k in range(ch.i2, ch.i2 + ch.chg2):
            slines[k].flag |= mask
    p_lno = 1
    for lno in range(cnt + 1):
        sl = slines[lno]
        sl.p_lno[n] = p_lno
        if sl.plost:
            sl.lost = _coalesce(sl.lost, sl.plost, n)
            sl.plost = []
        p_lno += sum(1 for _, m in sl.lost if m & mask)
        if lno < cnt and not (sl.flag & mask):
            p_lno += 1
    slines[cnt + 1].p_lno[n] = p_lno


def _interesting(sl, all_mask) -> bool:
    return bool(sl.flag & all_mask) or bool(sl.lost)


def _adjust_hunk_tail(slines, all_mask, hunk_begin, i):
    if hunk_begin + 1 <= i and not (slines[i - 1].flag & all_mask):
        i -= 1
    return i


def _find_next(slines, mark, i, cnt, look_for_uninteresting):
    while i <= cnt:
        if (not (slines[i].flag & mark)) if look_for_uninteresting else (slines[i].flag & mark):
            return i
        i += 1
    return i


def _give_context(slines, cnt, num_parent) -> bool:
    all_mask = (1 << num_parent) - 1
    mark = 1 << num_parent
    no_pre_delete = 2 << num_parent
    i = _find_next(slines, mark, 0, cnt, False)
    if cnt < i:
        return False
    while i <= cnt:
        j = i - CONTEXT if CONTEXT < i else 0
        while j < i:
            if not (slines[j].flag & mark):
                slines[j].flag |= no_pre_delete
            slines[j].flag |= mark
            j += 1
        while True:
            j = _find_next(slines, mark, i, cnt, True)
            if cnt < j:
                return True
            k = _find_next(slines, mark, j, cnt, False)
            j = _adjust_hunk_tail(slines, all_mask, i, j)
            if k < j + CONTEXT:
                while j < k:
                    slines[j].flag |= mark
                    j += 1
                i = k
                continue
            break
        i = k
        k = j + CONTEXT if j + CONTEXT < cnt + 1 else cnt + 1
        while j < k:
            slines[j].flag |= mark
            j += 1
    return True


def _make_hunks(slines, cnt, num_parent, dense) -> bool:
    all_mask = (1 << num_parent) - 1
    mark = 1 << num_parent
    for i in range(cnt + 1):
        if _interesting(slines[i], all_mask):
            slines[i].flag |= mark
        else:
            slines[i].flag &= ~mark
    if not dense:
        return _give_context(slines, cnt, num_parent)
    i = 0
    while i <= cnt:
        while i <= cnt and not (slines[i].flag & mark):
            i += 1
        if cnt < i:
            break
        hunk_begin = i
        j = i + 1
        while j <= cnt:
            if not (slines[j].flag & mark):
                la = _adjust_hunk_tail(slines, all_mask, hunk_begin, j)
                la = la + CONTEXT if la + CONTEXT < cnt + 1 else cnt + 1
                contin = False
                while la and j <= la - 1:
                    la -= 1
                    if slines[la].flag & mark:
                        contin = True
                        break
                if not contin:
                    break
                j = la
            j += 1
        hunk_end = j
        same_diff = 0
        has_interesting = False
        for j in range(i, hunk_end):
            this_diff = slines[j].flag & all_mask
            if this_diff:
                if not same_diff:
                    same_diff = this_diff
                elif same_diff != this_diff:
                    has_interesting = True
                    break
            for _, pmap in slines[j].lost:
                if not same_diff:
                    same_diff = pmap
                elif same_diff != pmap:
                    has_interesting = True
                    break
            if has_interesting:
                break
        if not has_interesting and same_diff != all_mask:
            for j in range(hunk_begin, hunk_end):
                slines[j].flag &= ~mark
        i = hunk_end
    return _give_context(slines, cnt, num_parent)


def _hunk_comment_line(bol) -> bool:
    return bool(bol) and (chr(bol[0]).isalpha() and bol[0] < 128 or bol[0] in b"_$")


def _dump(slines, cnt, num_parent) -> bytes:
    mark = 1 << num_parent
    no_pre_delete = 2 << num_parent
    out = []
    lno = 0
    while True:
        hunk_comment = None
        while lno <= cnt and not (slines[lno].flag & mark):
            if lno < cnt and _hunk_comment_line(slines[lno].bol):
                hunk_comment = slines[lno].bol
            lno += 1
        if cnt < lno:
            break
        hunk_end = lno + 1
        while hunk_end <= cnt and (slines[hunk_end].flag & mark):
            hunk_end += 1
        rlines = hunk_end - lno
        if cnt < hunk_end:
            rlines -= 1
        hdr = b"@" * (num_parent + 1)
        for n in range(num_parent):
            l0, l1 = slines[lno].p_lno[n], slines[hunk_end].p_lno[n]
            hdr += b" -%d,%d" % (l0, l1 - l0)
        hdr += b" +%d,%d " % (lno + 1, rlines) + b"@" * (num_parent + 1)
        if hunk_comment:
            # As git: the comment stops before its last non-space
            # character within the first 40 bytes.
            comment_end = 0
            for i, ch in enumerate(hunk_comment[:40]):
                if ch == 0x0A:
                    break
                if ch not in b" \t\r\x0b\x0c":
                    comment_end = i
            if comment_end:
                hdr += b" " + hunk_comment[:comment_end]
        out.append(hdr + b"\n")
        while lno < hunk_end:
            sl = slines[lno]
            lno += 1
            if not (sl.flag & no_pre_delete):
                for line, pmap in sl.lost:
                    cols = b"".join(b"-" if pmap & (1 << j) else b" " for j in range(num_parent))
                    out.append(cols + line + b"\n")
            if cnt < lno:
                break
            cols = b"".join(b"+" if sl.flag & (1 << j) else b" " for j in range(num_parent))
            out.append(cols + sl.bol + b"\n")
    return b"".join(out)


def combined_hunks(result: bytes, parents: list[bytes], dense: bool = True,
                   result_deleted: bool = False) -> bytes:
    """The hunks of a combined diff of `result` against `parents`."""
    lines = _split_result(result)
    cnt = len(lines)
    num_parent = len(parents)
    slines = [_SLine(lines[i] if i < cnt else None, num_parent) for i in range(cnt + 2)]
    if result_deleted:
        return b""
    recs = xdiff.split_lines(result)
    for n, pdata in enumerate(parents):
        dup = next((m for m in range(n) if parents[m] == pdata), None)
        if dup is not None:
            # reuse_combine_diff: an identical parent gets the same marks.
            src, dst = 1 << dup, 1 << n
            for sl in slines:
                if sl.flag & src:
                    sl.flag |= dst
                for ll in sl.lost:
                    if ll[1] & src:
                        ll[1] |= dst
                sl.p_lno[n] = sl.p_lno[dup]
            continue
        _add_parent(slines, cnt, recs, pdata, n, num_parent)
    if not _make_hunks(slines, cnt, num_parent, dense):
        return b""
    return _dump(slines, cnt, num_parent)


def combined_patch(repo, path: bytes, result_oid: str | None, result_mode: int, result_data: bytes,
                   parents: list[tuple[int, str]], parent_data: list[bytes], working_tree: bool) -> bytes:
    """`diff --cc <path>` with its header (show_combined_header)."""
    from pygit.diffout import _file_line, _quote_prefixed, abbrev
    deleted = result_mode == 0
    hunks = combined_hunks(result_data, parent_data, dense=True, result_deleted=deleted)
    mode_differs = any(m != result_mode for m, _ in parents)
    if not (hunks or mode_differs or working_tree):
        return b""
    q = _quote_prefixed(b"", path)
    out = [b"diff --cc " + q + b"\n"]
    out.append(b"index " + b",".join(abbrev(repo, o) for _, o in parents) + b".." +
               abbrev(repo, result_oid) + b"\n")
    added = False
    if mode_differs:
        added = not deleted and all(m == 0 for m, _ in parents)
        if added:
            out.append(b"new file mode %06o\n" % result_mode)
        else:
            line = b"deleted file " if deleted else b""
            line += b"mode " + b",".join(b"%06o" % m for m, _ in parents)
            if result_mode:
                line += b"..%06o" % result_mode
            out.append(line + b"\n")
    out.append(_file_line(b"---", b"a/", None if added else path))
    out.append(_file_line(b"+++", b"b/", None if deleted else path))
    out.append(hunks)
    return b"".join(out)

"""Rendering file pairs as git renders them: patches, --stat, --numstat,
--name-only, --name-status (diff.c's builtin_diff, show_stats and friends)."""

from __future__ import annotations

import os

from pygit import revparse, xdiff
from pygit.objects import ZERO_ID
from pygit.quote import quote_path
from pygit.treediff import FilePair, pprint_rename, shortstat


class Contents:
    """Loads blob contents for pair sides; work-tree sides come from files."""

    def __init__(self, repo):
        self.repo = repo
        self.extra: dict[str, bytes] = {}   # oid -> data for unwritten work-tree blobs

    def get(self, oid: str | None) -> bytes:
        if not oid or oid == ZERO_ID:
            return b""
        if oid in self.extra:
            return self.extra[oid]
        return self.repo.odb.read(oid)[1]


def abbrev(repo, oid: str | None, n: int = 7) -> bytes:
    if not oid or oid == ZERO_ID:
        return b"0" * n
    return revparse.short_id(repo, oid, n).encode()


def _quote_prefixed(prefix: bytes, path: bytes) -> bytes:
    """`a/path`, quoted as a whole when the path needs quoting."""
    q = quote_path(path)
    if q is path:
        return prefix + path
    return b'"' + prefix + q[1:]


def _file_line(marker: bytes, prefix: bytes, path: bytes | None) -> bytes:
    if path is None:
        return marker + b" /dev/null\n"
    name = _quote_prefixed(prefix, path)
    tab = b"\t" if b" " in path else b""
    return marker + b" " + name + tab + b"\n"


def patch(repo, pairs: list[FilePair], contents: Contents, context: int = 3,
          src_prefix: bytes = b"a/", dst_prefix: bytes = b"b/", abbrev_len: int = 7,
          full_index: bool = False) -> bytes:
    out = []
    for p in pairs:
        if p.status == "T":
            # A type change is shown as a deletion and an addition.
            out.append(_one_patch(repo, FilePair("D", p.old_path, None, p.old_mode, 0, p.old_oid, None),
                                  contents, context, src_prefix, dst_prefix, abbrev_len, full_index))
            out.append(_one_patch(repo, FilePair("A", None, p.new_path, 0, p.new_mode, None, p.new_oid),
                                  contents, context, src_prefix, dst_prefix, abbrev_len, full_index))
        else:
            out.append(_one_patch(repo, p, contents, context, src_prefix, dst_prefix, abbrev_len, full_index))
    return b"".join(out)


def _one_patch(repo, p: FilePair, contents: Contents, context, src_prefix, dst_prefix,
               abbrev_len, full_index) -> bytes:
    old_path = p.old_path if p.old_path is not None else p.new_path
    new_path = p.new_path if p.new_path is not None else p.old_path
    lines = [b"diff --git " + _quote_prefixed(src_prefix, old_path) + b" " +
             _quote_prefixed(dst_prefix, new_path) + b"\n"]
    ab = (lambda o: (o or ZERO_ID).encode()) if full_index else (lambda o: abbrev(repo, o, abbrev_len))
    if p.status == "A":
        lines.append(b"new file mode %06o\n" % p.new_mode)
    elif p.status == "D":
        lines.append(b"deleted file mode %06o\n" % p.old_mode)
    else:
        if p.old_mode != p.new_mode:
            lines.append(b"old mode %06o\nnew mode %06o\n" % (p.old_mode, p.new_mode))
        if p.status == "R":
            lines.append(b"similarity index %d%%\n" % p.score)
            lines.append(b"rename from " + quote_path(p.old_path) + b"\n")
            lines.append(b"rename to " + quote_path(p.new_path) + b"\n")
    if p.old_oid == p.new_oid and p.status not in "AD":
        return b"".join(lines)
    index = b"index " + ab(p.old_oid) + b".." + ab(p.new_oid)
    if p.status not in "AD" and p.old_mode == p.new_mode:
        index += b" %06o" % p.new_mode
    lines.append(index + b"\n")
    a = contents.get(p.old_oid)
    b = contents.get(p.new_oid)
    if p.old_mode == 0o160000 or p.new_mode == 0o160000:
        lines.append(_file_line(b"---", src_prefix, p.old_path))
        lines.append(_file_line(b"+++", dst_prefix, p.new_path))
        la = [b"Subproject commit " + p.old_oid.encode() + b"\n"] if p.old_oid else []
        lb = [b"Subproject commit " + p.new_oid.encode() + b"\n"] if p.new_oid else []
        lines.append(xdiff.unified(la, lb, xdiff.diff(la, lb), context))
        return b"".join(lines)
    if xdiff.is_binary(a) or xdiff.is_binary(b):
        an = b"/dev/null" if p.old_path is None or p.status == "A" else _quote_prefixed(src_prefix, old_path)
        bn = b"/dev/null" if p.new_path is None or p.status == "D" else _quote_prefixed(dst_prefix, new_path)
        lines.append(b"Binary files " + an + b" and " + bn + b" differ\n")
        return b"".join(lines)
    if not a and not b:
        return b"".join(lines)
    lines.append(_file_line(b"---", src_prefix, None if p.status == "A" else old_path))
    lines.append(_file_line(b"+++", dst_prefix, None if p.status == "D" else new_path))
    la, lb = xdiff.split_lines(a), xdiff.split_lines(b)
    lines.append(xdiff.unified(la, lb, xdiff.diff(la, lb), context))
    return b"".join(lines)


def _counts(p: FilePair, contents: Contents):
    """(added, deleted, binary) for stat output; binary counts are byte sizes."""
    a, b = contents.get(p.old_oid), contents.get(p.new_oid)
    if p.old_mode == 0o160000 or p.new_mode == 0o160000:
        return (1 if p.new_oid else 0), (1 if p.old_oid else 0), False
    if xdiff.is_binary(a) or xdiff.is_binary(b):
        return len(b), len(a), True
    if p.old_oid == p.new_oid:
        return 0, 0, False
    la, lb = xdiff.split_lines(a), xdiff.split_lines(b)
    ins, dels = xdiff.counts(xdiff.diff(la, lb))
    return ins, dels, False


def _stat_name(p: FilePair) -> bytes:
    if p.status == "R":
        return pprint_rename(quote_path(p.old_path), quote_path(p.new_path))
    return quote_path(p.path)


def _width(s: bytes) -> int:
    return len(s.decode("utf-8", "replace"))


def _scale_linear(it: int, width: int, max_change: int) -> int:
    if not it:
        return 0
    return 1 + (it * (width - 1) // max_change)


def stat(pairs: list[FilePair], contents: Contents, width: int | None = None,
         name_width: int = 0, graph_width: int = 0) -> bytes:
    """`--stat` (show_stats in diff.c) followed by the shortstat line."""
    if not pairs:
        return b""
    if width is None:
        try:
            width = int(os.environ.get("COLUMNS", "0")) or 80
        except ValueError:
            width = 80
    files = []
    max_len = max_change = bin_width = number_width = 0
    for p in pairs:
        added, deleted, binary = _counts(p, contents)
        name = _stat_name(p)
        files.append((name, added, deleted, binary))
        max_len = max(max_len, _width(name))
        if binary:
            w = 14 + len(str(added)) + len(str(deleted))
            bin_width = max(bin_width, w)
            number_width = 3
            continue
        max_change = max(max_change, added + deleted)
    number_width = max(number_width, len(str(max_change)))
    if width < 16 + 6 + number_width:
        width = 16 + 6 + number_width
    gw = max_change if max_change + 4 > bin_width else bin_width - 4
    if graph_width and graph_width < gw:
        gw = graph_width
    nw = name_width if 0 < name_width < max_len else max_len
    if nw + number_width + 6 + gw > width:
        if gw > width * 3 // 8 - number_width - 6:
            gw = width * 3 // 8 - number_width - 6
            if gw < 6:
                gw = 6
        if graph_width and gw > graph_width:
            gw = graph_width
        if nw > width - number_width - 6 - gw:
            nw = width - number_width - 6 - gw
        else:
            gw = width - number_width - 6 - nw
    out = []
    total_add = total_del = 0
    for name, added, deleted, binary in files:
        prefix = b""
        ln = nw
        shown = name
        name_len = _width(name)
        if nw < name_len:
            prefix = b"..."
            ln = max(nw - 3, 0)
            text = name.decode("utf-8", "replace")
            while len(text) > ln:
                text = text[1:]
            slash = text.find("/")
            if slash >= 0:
                text = text[slash:]
            shown = text.encode("utf-8")
        pad = b" " * max(ln - _width(shown), 0)
        head = b" " + prefix + shown + pad + b" |"
        if binary:
            line = head + b" %*s" % (number_width, b"Bin")
            if added or deleted:
                line += b" %d -> %d bytes" % (deleted, added)
            out.append(line + b"\n")
            continue
        total_add += added
        total_del += deleted
        add, dele = added, deleted
        if gw <= max_change:
            total = _scale_linear(add + dele, gw, max_change)
            if total < 2 and add and dele:
                total = 2
            if add < dele:
                add = _scale_linear(add, gw, max_change)
                dele = total - add
            else:
                dele = _scale_linear(dele, gw, max_change)
                add = total - dele
        line = head + b" %*d" % (number_width, added + deleted)
        if added + deleted:
            line += b" "
        line += b"+" * add + b"-" * dele
        out.append(line + b"\n")
    out.append(shortstat(len(pairs), total_add, total_del))
    return b"".join(out)


def shortstat_for(pairs, contents) -> bytes:
    if not pairs:
        return b""
    ins = dels = 0
    for p in pairs:
        a, d, binary = _counts(p, contents)
        if not binary:
            ins += a
            dels += d
    return shortstat(len(pairs), ins, dels)


def numstat(pairs, contents, z: bool = False) -> bytes:
    out = []
    for p in pairs:
        a, d, binary = _counts(p, contents)
        nums = b"-\t-\t" if binary else b"%d\t%d\t" % (a, d)
        if p.status == "R":
            if z:
                out.append(nums + b"\0" + p.old_path + b"\0" + p.new_path + b"\0")
            else:
                out.append(nums + pprint_rename(quote_path(p.old_path), quote_path(p.new_path)) + b"\n")
        else:
            out.append(nums + (p.path + b"\0" if z else quote_path(p.path) + b"\n"))
    return b"".join(out)


def name_only(pairs, z: bool = False) -> bytes:
    end = b"\0" if z else b"\n"
    return b"".join((p.path if z else quote_path(p.path)) + end for p in pairs)


def name_status(pairs, z: bool = False) -> bytes:
    out = []
    for p in pairs:
        if p.status == "R":
            code = b"R%03d" % p.score
            if z:
                out.append(code + b"\0" + p.old_path + b"\0" + p.new_path + b"\0")
            else:
                out.append(code + b"\t" + quote_path(p.old_path) + b"\t" + quote_path(p.new_path) + b"\n")
        else:
            if z:
                out.append(p.status.encode() + b"\0" + p.path + b"\0")
            else:
                out.append(p.status.encode() + b"\t" + quote_path(p.path) + b"\n")
    return b"".join(out)

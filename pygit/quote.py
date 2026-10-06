"""Path quoting as git's quote_c_style does it (core.quotePath = true)."""

_ESCAPES = {
    0x07: b"\\a", 0x08: b"\\b", 0x09: b"\\t", 0x0A: b"\\n",
    0x0B: b"\\v", 0x0C: b"\\f", 0x0D: b"\\r", 0x22: b'\\"', 0x5C: b"\\\\",
}


def _needs_quote(b: int, quote_high: bool) -> bool:
    return b < 0x20 or b == 0x22 or b == 0x5C or b == 0x7F or (quote_high and b >= 0x80)


def quote_path(path: bytes, quote_high: bool = True) -> bytes:
    """Return `path` unchanged, or wrapped in quotes with C-style escapes."""
    if not any(_needs_quote(b, quote_high) for b in path):
        return path
    out = [b'"']
    for b in path:
        if b in _ESCAPES:
            out.append(_ESCAPES[b])
        elif _needs_quote(b, quote_high):
            out.append(b"\\%03o" % b)
        else:
            out.append(bytes([b]))
    out.append(b'"')
    return b"".join(out)


_UNESCAPES = {ord(k[1:]): v for v, k in ((bytes([b]), e) for b, e in _ESCAPES.items())}


def unquote_path(quoted: bytes) -> bytes:
    """Inverse of quote_path for a string wrapped in double quotes."""
    if not (quoted.startswith(b'"') and quoted.endswith(b'"')):
        return quoted
    s = quoted[1:-1]
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c != 0x5C:
            out.append(c)
            i += 1
            continue
        n = s[i + 1]
        if 0x30 <= n <= 0x37:
            out.append(int(s[i + 1:i + 4], 8))
            i += 4
        else:
            out += _UNESCAPES.get(n, bytes([n]))
            i += 2
    return bytes(out)

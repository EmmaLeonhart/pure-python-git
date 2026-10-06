"""Formatting commits the way `git log` and `git commit` show them."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from pygit.objects import Commit, Signature

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _local(sig: Signature) -> datetime:
    tz = timezone(timedelta(minutes=sig.tz_offset_minutes))
    return datetime.fromtimestamp(sig.timestamp, tz)


def date_normal(sig: Signature) -> bytes:
    """`Tue Nov 14 23:13:20 2023 +0100` (DATE_NORMAL, in the signature's zone)."""
    d = _local(sig)
    return (f"{_DAYS[d.weekday()]} {_MONTHS[d.month - 1]} {d.day} "
            f"{d.hour:02d}:{d.minute:02d}:{d.second:02d} {d.year} ").encode() + sig.tz


def date_iso(sig: Signature) -> bytes:
    d = _local(sig)
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} {d.hour:02d}:{d.minute:02d}:{d.second:02d} ".encode() + sig.tz


def date_iso_strict(sig: Signature) -> bytes:
    d = _local(sig)
    tz = sig.tz.decode()
    tzs = "Z" if tz in ("+0000",) else f"{tz[:3]}:{tz[3:]}"
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}T{d.hour:02d}:{d.minute:02d}:{d.second:02d}{tzs}".encode()


def date_short(sig: Signature) -> bytes:
    d = _local(sig)
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}".encode()


def date_rfc2822(sig: Signature) -> bytes:
    d = _local(sig)
    return (f"{_DAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year} "
            f"{d.hour:02d}:{d.minute:02d}:{d.second:02d} ").encode() + sig.tz


def format_date(sig: Signature, mode: str = "default") -> bytes:
    if mode in ("default", "normal"):
        return date_normal(sig)
    if mode == "iso" or mode == "iso8601":
        return date_iso(sig)
    if mode in ("iso-strict", "iso8601-strict"):
        return date_iso_strict(sig)
    if mode == "short":
        return date_short(sig)
    if mode == "raw":
        return b"%d " % sig.timestamp + sig.tz
    if mode == "unix":
        return b"%d" % sig.timestamp
    if mode == "rfc" or mode == "rfc2822":
        return date_rfc2822(sig)
    return date_normal(sig)


def _is_blank(line: bytes) -> bool:
    return not line.strip(b" \t\n\r\x0b\x0c")


def split_message(message: bytes) -> tuple[bytes, bytes]:
    """(subject, body) as %s and %b see them.

    The subject is the first paragraph with its lines joined by spaces;
    the body is everything after it, leading blank lines skipped.
    """
    lines = message.split(b"\n")
    i = 0
    while i < len(lines) and _is_blank(lines[i]):
        i += 1
    subj = []
    while i < len(lines) and not _is_blank(lines[i]):
        subj.append(lines[i].rstrip(b" \t\n\r\x0b\x0c"))  # leading space is kept
        i += 1
    while i < len(lines) and _is_blank(lines[i]):
        i += 1
    body = b"\n".join(lines[i:])
    return b" ".join(subj), body


def _tabexpand(line: bytes, tabstop: int = 8) -> bytes:
    if b"\t" not in line:
        return line
    out = bytearray()
    width = 0
    text = line.decode("utf-8", "replace")
    for ch in text:
        if ch == "\t":
            n = tabstop - width % tabstop
            out += b" " * n
            width += n
        else:
            out += ch.encode("utf-8")
            width += 1
    return bytes(out)


def indented_message(message: bytes, indent: int = 4, expand_tabs: bool = True) -> bytes:
    """pp_remainder: skip leading blank lines, indent every line, rtrim the end."""
    lines = message.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    out = []
    first = True
    for line in lines:
        if _is_blank(line) and first:
            continue
        first = False
        content = _tabexpand(line) if expand_tabs else line
        out.append(b" " * indent + content + b"\n")
    return b"".join(out)


def medium(repo, oid: str, c: Commit, abbrev_parents) -> bytes:
    """The default `git log` entry (without the separating blank line)."""
    out = [b"commit " + oid.encode() + b"\n"]
    if len(c.parents) > 1:
        out.append(b"Merge: " + b" ".join(abbrev_parents(p).encode() for p in c.parents) + b"\n")
    out.append(b"Author: " + c.author.name + b" <" + c.author.email + b">\n")
    out.append(b"Date:   " + date_normal(c.author) + b"\n")
    out.append(b"\n")
    out.append(indented_message(c.message))
    return _rtrim_entry(b"".join(out))


def _rtrim_entry(s: bytes) -> bytes:
    return s.rstrip(b" \t\n\r\x0b\x0c") + b"\n"


def full(repo, oid, c, abbrev_parents, fuller=False) -> bytes:
    out = [b"commit " + oid.encode() + b"\n"]
    if len(c.parents) > 1:
        out.append(b"Merge: " + b" ".join(abbrev_parents(p).encode() for p in c.parents) + b"\n")
    if fuller:
        out.append(b"Author:     " + c.author.name + b" <" + c.author.email + b">\n")
        out.append(b"AuthorDate: " + date_normal(c.author) + b"\n")
        out.append(b"Commit:     " + c.committer.name + b" <" + c.committer.email + b">\n")
        out.append(b"CommitDate: " + date_normal(c.committer) + b"\n")
    else:
        out.append(b"Author: " + c.author.name + b" <" + c.author.email + b">\n")
        out.append(b"Commit: " + c.committer.name + b" <" + c.committer.email + b">\n")
    out.append(b"\n")
    out.append(indented_message(c.message))
    return _rtrim_entry(b"".join(out))


def short(repo, oid, c, abbrev_parents) -> bytes:
    out = [b"commit " + oid.encode() + b"\n"]
    if len(c.parents) > 1:
        out.append(b"Merge: " + b" ".join(abbrev_parents(p).encode() for p in c.parents) + b"\n")
    out.append(b"Author: " + c.author.name + b" <" + c.author.email + b">\n")
    out.append(b"\n")
    # The short format shows the first paragraph, each line indented.
    first_para = []
    for line in c.message.split(b"\n"):
        if _is_blank(line):
            if first_para:
                break
            continue
        first_para.append(line)
    out.append(indented_message(b"\n".join(first_para)))
    return _rtrim_entry(b"".join(out))


_PLACEHOLDER = re.compile(rb"%(?:\(([^)]*)\)|([aAcC][nNeEdDtiIrsh]|[HhTtPpsbBnfeN%]|x[0-9a-fA-F]{2}|C\([^)]*\)|C(?:red|green|blue|reset)))")


def format_placeholders(fmt: bytes, oid: str, c: Commit, abbrev) -> bytes:
    """Expand a --format string (the commonly used placeholders)."""
    subj, body = split_message(c.message)

    def person(sig: Signature, code: bytes) -> bytes:
        k = code[1:2]
        if k == b"n" or k == b"N":
            return sig.name
        if k == b"e" or k == b"E":
            return sig.email
        if k == b"d":
            return date_normal(sig)
        if k == b"D":
            return date_rfc2822(sig)
        if k == b"t":
            return b"%d" % sig.timestamp
        if k == b"i":
            return date_iso(sig)
        if k == b"I":
            return date_iso_strict(sig)
        if k == b"s":
            return date_short(sig)
        if k == b"r":
            return date_normal(sig)  # relative dates are not supported
        return b"%" + code

    def repl(m):
        code = m.group(2)
        if code is None:
            return m.group(0)
        if code == b"%":
            return b"%"
        if code == b"n":
            return b"\n"
        if code.startswith(b"x"):
            return bytes([int(code[1:], 16)])
        if code.startswith(b"C"):
            return b""
        if code[:1] in b"aA" and len(code) == 2:
            return person(c.author, code)
        if code[:1] in b"cC" and len(code) == 2:
            return person(c.committer, code)
        if code == b"H":
            return oid.encode()
        if code == b"h":
            return abbrev(oid).encode()
        if code == b"T":
            return c.tree.encode()
        if code == b"t":
            return abbrev(c.tree).encode()
        if code == b"P":
            return b" ".join(p.encode() for p in c.parents)
        if code == b"p":
            return b" ".join(abbrev(p).encode() for p in c.parents)
        if code == b"s":
            return subj
        if code == b"f":
            return sanitized_subject(c.message)
        if code == b"b":
            return body
        if code == b"B":
            return c.message
        if code == b"e":
            return b""
        if code == b"N":
            return b""
        return m.group(0)

    return _PLACEHOLDER.sub(repl, fmt)


def sanitized_subject(message: bytes) -> bytes:
    """%f: the first line of the subject as a file-name-safe string
    (format_sanitized_subject in git's pretty.c)."""
    i = 0
    lines = message.split(b"\n")
    while i < len(lines) and _is_blank(lines[i]):
        i += 1
    line = lines[i] if i < len(lines) else b""
    out = bytearray()
    space = 2
    j = 0
    while j < len(line):
        ch = line[j]
        if (0x30 <= ch <= 0x39) or (0x41 <= ch <= 0x5A) or (0x61 <= ch <= 0x7A) or ch in b"._":
            if space == 1:
                out.append(0x2D)
            space = 0
            out.append(ch)
            if ch == 0x2E:
                while j + 1 < len(line) and line[j + 1] == 0x2E:
                    j += 1
        else:
            space |= 1
        j += 1
    while out and out[-1] in b".-":
        out.pop()
    return bytes(out)


def cleanup_message(msg: bytes, mode: str = "whitespace", comment: bytes = b"#") -> bytes:
    """git's strbuf_stripspace: trailing whitespace off every line, runs of
    blank lines collapsed, leading/trailing blank lines dropped; "strip"
    also removes comment lines."""
    if mode == "verbatim":
        return msg
    out = []
    empties = 0
    for line in msg.split(b"\n"):
        if mode == "strip" and line.startswith(comment):
            continue
        line = line.rstrip(b" \t\n\r\x0b\x0c")
        if not line:
            empties += 1
            continue
        if empties and out:
            out.append(b"")
        empties = 0
        out.append(line)
    if not out:
        return b""
    return b"\n".join(out) + b"\n"

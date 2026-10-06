"""Line-ending conversion between the work tree and the repository:
the CRLF part of git's convert.c (`core.autocrlf`, `core.eol`, and the
`text`/`eol` attributes).
"""

from __future__ import annotations

import os
import sys

BINARY, TEXT, TEXT_INPUT, TEXT_CRLF, AUTO, AUTO_INPUT, AUTO_CRLF, UNDEFINED = range(8)
NATIVE_CRLF = os.name == "nt"  # Git for Windows is built with NATIVE_CRLF


class Stats:
    __slots__ = ("nul", "lonecr", "lonelf", "crlf", "printable", "nonprintable")


def gather_stats(buf: bytes) -> Stats:
    s = Stats()
    s.nul = s.lonecr = s.lonelf = s.crlf = s.printable = s.nonprintable = 0
    i, size = 0, len(buf)
    while i < size:
        c = buf[i]
        if c == 0x0D:
            if i + 1 < size and buf[i + 1] == 0x0A:
                s.crlf += 1
                i += 1
            else:
                s.lonecr += 1
        elif c == 0x0A:
            s.lonelf += 1
        elif c == 127:
            s.nonprintable += 1
        elif c < 32:
            if c in (0x08, 0x09, 0x1B, 0x0C):
                s.printable += 1
            else:
                if c == 0:
                    s.nul += 1
                s.nonprintable += 1
        else:
            s.printable += 1
        i += 1
    if size and buf[-1] == 0x1A:
        s.nonprintable -= 1
    return s


def is_binary(s: Stats) -> bool:
    return bool(s.lonecr or s.nul or (s.printable >> 7) < s.nonprintable)


def _autocrlf(repo) -> str:
    v = (repo.config.get("core.autocrlf") or "false").lower()
    if v == "input":
        return "input"
    from pygit.config import parse_bool
    try:
        return "true" if parse_bool(v) else "false"
    except Exception:
        return "false"


def _text_eol_is_crlf(repo) -> bool:
    auto = _autocrlf(repo)
    if auto == "true":
        return True
    if auto == "input":
        return False
    eol = (repo.config.get("core.eol") or "").lower()
    if eol == "crlf":
        return True
    if eol == "lf":
        return False
    return NATIVE_CRLF


def crlf_action(repo, attrs: dict) -> int:
    """convert_attrs(): the action for a path's attributes."""
    text = attrs.get("text")
    if text is True:
        action = TEXT
    elif text is False:
        action = BINARY
    elif text == "auto":
        action = AUTO
    elif text == "input":
        action = TEXT_INPUT
    else:
        crlf = attrs.get("crlf")  # the legacy attribute
        action = {True: TEXT, False: BINARY, "input": TEXT_INPUT}.get(crlf, UNDEFINED)
    if action != BINARY:
        eol = attrs.get("eol")
        if action == AUTO and eol == "lf":
            action = AUTO_INPUT
        elif action == AUTO and eol == "crlf":
            action = AUTO_CRLF
        elif eol == "lf":
            action = TEXT_INPUT
        elif eol == "crlf":
            action = TEXT_CRLF
    if action == TEXT:
        action = TEXT_CRLF if _text_eol_is_crlf(repo) else TEXT_INPUT
    if action == UNDEFINED:
        action = {"false": BINARY, "true": AUTO_CRLF, "input": AUTO_INPUT}[_autocrlf(repo)]
    return action


def _output_crlf(repo, action: int) -> bool:
    if action in (TEXT_CRLF, AUTO_CRLF, UNDEFINED):
        return True
    if action in (BINARY, TEXT_INPUT, AUTO_INPUT):
        return False
    return _text_eol_is_crlf(repo)


def _will_convert_lf_to_crlf(repo, s: Stats, action: int) -> bool:
    if not _output_crlf(repo, action) or not s.lonelf:
        return False
    if action in (AUTO, AUTO_INPUT, AUTO_CRLF):
        if s.lonecr or s.crlf or is_binary(s):
            return False
    return True


class Converter:
    """Per-repository conversion, caching the attribute rules."""

    def __init__(self, repo, attr_reader=None):
        self.repo = repo
        from pygit.attributes import AttrRules
        self.rules = AttrRules(repo, attr_reader)
        self.auto = _autocrlf(repo)
        self.safecrlf = (repo.config.get("core.safecrlf") or "warn").lower()

    def action(self, path: bytes) -> int:
        return crlf_action(self.repo, self.rules.get(path))

    def to_git(self, path: bytes, data: bytes, index_has_cr=None, warn: bool = False) -> bytes:
        """The clean direction. `index_has_cr()`, if given, says whether the
        index version contains a CR (git then leaves "auto" files alone)."""
        action = self.action(path)
        if action == BINARY or not data:
            return data
        s = gather_stats(data)
        convert = bool(s.crlf)
        if action in (AUTO, AUTO_INPUT, AUTO_CRLF):
            if is_binary(s):
                return data
            if convert and index_has_cr is not None and index_has_cr():
                convert = False
        if warn and self.safecrlf not in ("false", "0", "no", "off"):
            new_lonelf = s.lonelf + (s.crlf if convert else 0)
            new_crlf = 0 if convert else s.crlf
            ns = Stats()
            ns.nul, ns.lonecr, ns.printable, ns.nonprintable = s.nul, s.lonecr, s.printable, s.nonprintable
            ns.lonelf, ns.crlf = new_lonelf, new_crlf
            if _will_convert_lf_to_crlf(self.repo, ns, action):
                ns.crlf += ns.lonelf
                ns.lonelf = 0
            name = path.decode("utf-8", "replace")
            if s.crlf and not ns.crlf:
                self._warn(f"in the working copy of '{name}', CRLF will be replaced by LF the next time Git touches it")
            elif s.lonelf and not ns.lonelf:
                self._warn(f"in the working copy of '{name}', LF will be replaced by CRLF the next time Git touches it")
        if not convert:
            return data
        return data.replace(b"\r\n", b"\n")

    def _warn(self, msg: str) -> None:
        if self.safecrlf in ("true", "1", "yes", "on"):
            from pygit.errors import GitError
            raise GitError(msg.replace("in the working copy of", "").strip())
        sys.stdout.flush()
        sys.stderr.buffer.write(b"warning: " + msg.encode("utf-8") + b"\n")
        sys.stderr.buffer.flush()

    def to_worktree(self, path: bytes, data: bytes) -> bytes:
        """The smudge direction: LF to CRLF where git would do it."""
        if not data:
            return data
        action = self.action(path)
        if not _output_crlf(self.repo, action):
            return data
        s = gather_stats(data)
        if not _will_convert_lf_to_crlf(self.repo, s, action):
            return data
        # Every LF not already preceded by CR becomes CRLF.
        out = bytearray()
        prev = 0
        for c in data:
            if c == 0x0A and prev != 0x0D:
                out.append(0x0D)
            out.append(c)
            prev = c
        return bytes(out)


def converter(repo) -> Converter:
    """One Converter per Repo object (attribute files are read once)."""
    c = getattr(repo, "_converter", None)
    if c is None:
        c = Converter(repo)
        repo._converter = c
    return c

"""`.gitattributes`: which attributes apply to a path.

Sources, lowest priority first: `core.attributesFile` (default
`$XDG_CONFIG_HOME/git/attributes`), the work tree's `.gitattributes` files
from the root down (deeper wins), then `$GIT_DIR/info/attributes`. Within a
file a later line wins. Patterns follow the `.gitignore` rules, without
negation. `[attr]name ...` defines a macro (only at the top level); the
built-in `binary` macro is `-diff -merge -text`.

Values: True (set), False (unset, `-attr`), a string (`attr=value`);
`!attr` returns an attribute to unspecified.
"""

from __future__ import annotations

import os
from pathlib import Path

from pygit.ignore import parse_patterns

BUILTIN_MACROS = {"binary": [("diff", False), ("merge", False), ("text", False)]}
UNSPECIFIED = object()


def _parse_states(tokens: list[bytes]) -> list[tuple[str, object]]:
    out = []
    for tok in tokens:
        t = tok.decode("utf-8", "replace")
        if t.startswith("-"):
            out.append((t[1:], False))
        elif t.startswith("!"):
            out.append((t[1:], UNSPECIFIED))
        elif "=" in t:
            k, _, v = t.partition("=")
            out.append((k, v))
        else:
            out.append((t, True))
    return out


def _split_line(line: bytes) -> tuple[bytes, list[bytes]] | None:
    line = line.strip(b" \t\r")
    if not line or line.startswith(b"#"):
        return None
    if line.startswith(b'"'):
        from pygit.quote import unquote_path
        end = 1
        while end < len(line) and not (line[end] == 0x22 and line[end - 1] != 0x5C):
            end += 1
        pattern = unquote_path(line[:end + 1])
        rest = line[end + 1:]
    else:
        parts = line.split(None, 1)
        pattern = parts[0]
        rest = parts[1] if len(parts) > 1 else b""
    return pattern, rest.split()


class AttrRules:
    def __init__(self, repo, reader=None):
        """`reader(dir)` returns the bytes of `<dir>/.gitattributes` or None;
        by default the work tree is read (and the index when a file is missing)."""
        self.repo = repo
        self.reader = reader or self._read_worktree_or_index
        self.macros = dict(BUILTIN_MACROS)
        self.order: list[str] = []          # attribute names in registration order
        for name, states in BUILTIN_MACROS.items():
            self._register(name)
            for k, _ in states:
                self._register(k)
        self._dirs: dict[bytes, list] = {}
        self._index = None
        self.global_rules = []
        cfg = repo.config.get("core.attributesFile")
        if cfg:
            gpath = Path(os.path.expanduser(cfg))
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
                os.environ.get("HOME") or os.path.expanduser("~"), ".config")
            gpath = Path(xdg) / "git" / "attributes"
        if gpath.is_file():
            self.global_rules = self._parse(gpath.read_bytes(), b"", allow_macros=True)
        self.info_rules = []
        info = repo.gitdir / "info" / "attributes"
        if info.is_file():
            self.info_rules = self._parse(info.read_bytes(), b"", allow_macros=True)

    def _register(self, name: str) -> None:
        if name not in self.order:
            self.order.append(name)

    def _read_worktree_or_index(self, d: bytes):
        if self.repo.worktree is not None:
            f = self.repo.worktree / os.fsdecode(d) / ".gitattributes" if d else self.repo.worktree / ".gitattributes"
            try:
                return f.read_bytes()
            except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
                pass
        if self._index is None:
            from pygit.index import Index
            try:
                self._index = Index.read(self.repo)
            except Exception:
                self._index = False
        if self._index:
            e = self._index.get((d + b"/" if d else b"") + b".gitattributes")
            if e is not None:
                return self.repo.odb.read(e.oid)[1]
        return None

    def _parse(self, data: bytes, base: bytes, allow_macros: bool):
        rules = []
        for line in data.split(b"\n"):
            got = _split_line(line)
            if got is None:
                continue
            pattern, tokens = got
            if pattern.startswith(b"[attr]"):
                if allow_macros:
                    name = pattern[6:].decode("utf-8", "replace")
                    self._register(name)
                    states = _parse_states(tokens)
                    for k, _ in states:
                        self._register(k)
                    self.macros[name] = states
                continue
            if pattern.startswith(b"!"):
                continue  # negative patterns are ignored, as in git
            pats = parse_patterns(pattern, base, ".gitattributes")
            if not pats:
                continue
            states = _parse_states(tokens)
            for k, _ in states:
                self._register(k)
            rules.append((pats[0], states))
        return rules

    def _dir_rules(self, d: bytes):
        if d not in self._dirs:
            data = self.reader(d)
            self._dirs[d] = self._parse(data, d + b"/" if d else b"", allow_macros=(d == b"")) if data else []
        return self._dirs[d]

    def _expand(self, name, value, result):
        result[name] = value
        if name in self.macros and value is True:
            for k, v in self.macros[name]:
                self._expand(k, v, result)
        elif name in self.macros and value is False:
            for k, _ in self.macros[name]:
                result[k] = False

    def get(self, path: bytes) -> dict:
        """All attributes that apply to `path` (unspecified ones omitted)."""
        result = {}
        parts = path.split(b"/")
        sources = [self.global_rules]
        for depth in range(len(parts)):
            sources.append(self._dir_rules(b"/".join(parts[:depth])))
        sources.append(self.info_rules)
        for rules in sources:
            for pat, states in rules:
                if pat.matches(path, False, False):
                    for k, v in states:
                        if v is UNSPECIFIED:
                            result.pop(k, None)
                        else:
                            self._expand(k, v, result)
        return result

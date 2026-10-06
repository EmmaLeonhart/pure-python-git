"""`.gitignore` rules and git's wildmatch.

Sources, highest priority first (the last matching pattern within the
highest-priority source that matches decides):
  1. `.gitignore` files, the deepest directory first;
  2. `$GIT_DIR/info/exclude`;
  3. the file named by `core.excludesFile` (default
     `$XDG_CONFIG_HOME/git/ignore`).
A path inside an excluded directory is excluded, whatever the patterns say
about the path itself: git does not descend into ignored directories.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# -- wildmatch (a port of git's wildmatch.c) -----------------------------------------

_MATCH, _NOMATCH, _ABORT_ALL, _ABORT_TO_STARSTAR = 1, 0, -1, -2

_CLASSES = {
    b"alnum": lambda c: chr(c).isalnum() and c < 128,
    b"alpha": lambda c: chr(c).isalpha() and c < 128,
    b"blank": lambda c: c in (0x20, 0x09),
    b"cntrl": lambda c: c < 0x20 or c == 0x7F,
    b"digit": lambda c: 0x30 <= c <= 0x39,
    b"graph": lambda c: 0x21 <= c <= 0x7E,
    b"lower": lambda c: 0x61 <= c <= 0x7A,
    b"print": lambda c: 0x20 <= c <= 0x7E,
    b"punct": lambda c: 0x21 <= c <= 0x7E and not chr(c).isalnum(),
    b"space": lambda c: c in b" \t\n\r\f\v",
    b"upper": lambda c: 0x41 <= c <= 0x5A,
    b"xdigit": lambda c: chr(c) in "0123456789abcdefABCDEF",
}


def _lower(c: int) -> int:
    return c + 32 if 0x41 <= c <= 0x5A else c


def _at(s: bytes, i: int) -> int:
    """s[i], or 0 past the end (C's NUL terminator)."""
    return s[i] if i < len(s) else 0


_GLOB_SPECIAL = frozenset(b"*?[\\")


def _dowild(p: bytes, pi: int, t: bytes, ti: int, icase: bool, pathname: bool) -> int:
    """Line-by-line port of dowild() from git's wildmatch.c."""
    while (p_ch := _at(p, pi)) != 0:
        t_ch = _at(t, ti)
        if t_ch == 0 and p_ch != 0x2A:
            return _ABORT_ALL
        if icase:
            t_ch = _lower(t_ch)
            p_ch = _lower(p_ch)
        if p_ch == 0x5C or p_ch not in (0x3F, 0x2A, 0x5B):  # '\\' or a literal
            if p_ch == 0x5C:
                pi += 1
                p_ch = _at(p, pi)
            if t_ch != p_ch:
                return _NOMATCH
        elif p_ch == 0x3F:  # '?'
            if pathname and t_ch == 0x2F:
                return _NOMATCH
        elif p_ch == 0x2A:  # '*'
            pi += 1
            if _at(p, pi) == 0x2A:
                prev_pi = pi - 2
                while True:
                    pi += 1
                    if _at(p, pi) != 0x2A:
                        break
                if (prev_pi < 0 or p[prev_pi] == 0x2F) and (
                        _at(p, pi) in (0, 0x2F) or (_at(p, pi) == 0x5C and _at(p, pi + 1) == 0x2F)):
                    if _at(p, pi) == 0x2F and _dowild(p, pi + 1, t, ti, icase, pathname) == _MATCH:
                        return _MATCH
                    match_slash = True
                else:
                    match_slash = False
            else:
                match_slash = not pathname
            if _at(p, pi) == 0:
                if not match_slash and t.find(b"/", ti) >= 0:
                    return _ABORT_TO_STARSTAR
                return _MATCH
            if not match_slash and _at(p, pi) == 0x2F:
                slash = t.find(b"/", ti)
                if slash < 0:
                    return _ABORT_ALL
                # The slash is consumed by the loop increment below.
                ti = slash
                pi += 1
                ti += 1
                continue
            while True:
                if t_ch == 0:
                    break
                if _at(p, pi) not in _GLOB_SPECIAL:
                    want = _at(p, pi)
                    if icase:
                        want = _lower(want)
                    while True:
                        t_ch = _at(t, ti)
                        if t_ch == 0 or (not match_slash and t_ch == 0x2F):
                            break
                        if icase:
                            t_ch = _lower(t_ch)
                        if t_ch == want:
                            break
                        ti += 1
                    if t_ch != want:
                        return _NOMATCH
                matched = _dowild(p, pi, t, ti, icase, pathname)
                if matched != _NOMATCH:
                    if not match_slash or matched != _ABORT_TO_STARSTAR:
                        return matched
                elif not match_slash and t_ch == 0x2F:
                    return _ABORT_TO_STARSTAR
                ti += 1
                t_ch = _at(t, ti)
            return _ABORT_ALL
        else:  # '['
            pi += 1
            p_ch = _at(p, pi)
            if p_ch == 0x5E:  # '^' means the same as '!'
                p_ch = 0x21
            negated = p_ch == 0x21
            if negated:
                pi += 1
                p_ch = _at(p, pi)
            prev_ch = 0
            matched = False
            while True:
                if p_ch == 0:
                    return _ABORT_ALL
                if p_ch == 0x5C:
                    pi += 1
                    p_ch = _at(p, pi)
                    if p_ch == 0:
                        return _ABORT_ALL
                    if t_ch == p_ch:
                        matched = True
                elif p_ch == 0x2D and prev_ch and _at(p, pi + 1) and _at(p, pi + 1) != 0x5D:
                    pi += 1
                    p_ch = _at(p, pi)
                    if p_ch == 0x5C:
                        pi += 1
                        p_ch = _at(p, pi)
                        if p_ch == 0:
                            return _ABORT_ALL
                    if prev_ch <= t_ch <= p_ch:
                        matched = True
                    elif icase and 0x61 <= t_ch <= 0x7A and prev_ch <= t_ch - 32 <= p_ch:
                        matched = True
                    p_ch = 0
                elif p_ch == 0x5B and _at(p, pi + 1) == 0x3A:  # [:class:]
                    s = pi + 2
                    end = s
                    while _at(p, end) and _at(p, end) != 0x5D:
                        end += 1
                    if not _at(p, end):
                        return _ABORT_ALL
                    if end - s == 0 or _at(p, end - 1) != 0x3A:
                        # Didn't find ":]", so treat it like a normal set.
                        pi = s - 2
                        p_ch = 0x5B
                        if t_ch == p_ch:
                            matched = True
                        prev_ch = p_ch
                        pi += 1
                        p_ch = _at(p, pi)
                        if p_ch == 0x5D:
                            break
                        continue
                    cls = p[s:end - 1]
                    fn = _CLASSES.get(cls)
                    if fn is None:
                        return _ABORT_ALL
                    if fn(t_ch) or (icase and cls == b"upper" and _CLASSES[b"lower"](t_ch)):
                        matched = True
                    pi = end
                    p_ch = 0
                elif t_ch == p_ch:
                    matched = True
                prev_ch = p_ch
                pi += 1
                p_ch = _at(p, pi)
                if p_ch == 0x5D:
                    break
            if matched == negated or (pathname and t_ch == 0x2F):
                return _NOMATCH
        pi += 1
        ti += 1
    return _NOMATCH if _at(t, ti) else _MATCH


def wildmatch(pattern: bytes, text: bytes, icase: bool = False, pathname: bool = True) -> bool:
    return _dowild(pattern, 0, text, 0, icase, pathname) == _MATCH


# -- patterns ----------------------------------------------------------------------

@dataclass
class Pattern:
    pattern: bytes      # as matched (leading '/' and trailing '/' removed)
    raw: bytes          # the line as written (for check-ignore -v)
    base: bytes         # directory of the .gitignore, "" or "dir/"
    source: str         # file the pattern came from, as git names it
    lineno: int
    negated: bool
    dir_only: bool
    no_slash: bool      # match the basename only

    def matches(self, path: bytes, is_dir: bool, icase: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if self.no_slash:
            name = path.rsplit(b"/", 1)[-1]
            return wildmatch(self.pattern, name, icase, pathname=True)
        if self.base:
            if not path.startswith(self.base):
                return False
            path = path[len(self.base):]
        return wildmatch(self.pattern, path, icase, pathname=True)


def parse_patterns(data: bytes, base: bytes, source: str) -> list[Pattern]:
    out = []
    for lineno, line in enumerate(data.split(b"\n"), 1):
        if line.endswith(b"\r"):
            line = line[:-1]
        if not line or line.startswith(b"#"):
            continue
        # Trailing spaces are dropped unless escaped with a backslash.
        while line.endswith(b" ") and not line.endswith(b"\\ "):
            line = line[:-1]
        raw = line  # check-ignore -v shows the pattern after this trim
        if line.endswith(b"\\ "):
            line = line[:-2] + b" "
        if not line:
            continue
        negated = False
        if line.startswith(b"!"):
            negated = True
            line = line[1:]
        elif line.startswith(b"\\!") or line.startswith(b"\\#"):
            line = line[1:]
        dir_only = line.endswith(b"/")
        if dir_only:
            line = line.rstrip(b"/")
        if not line:
            continue
        no_slash = b"/" not in line
        if line.startswith(b"/"):
            line = line[1:]
        out.append(Pattern(line, raw, base, source, lineno, negated, dir_only, no_slash))
    return out


class IgnoreRules:
    """Ignore decisions for a work tree, loading `.gitignore` files lazily."""

    def __init__(self, repo, extra_patterns: list[bytes] | None = None):
        self.repo = repo
        self.root = repo.worktree
        self.icase = repo.config.get_bool("core.ignoreCase", False)
        self._dir_patterns: dict[bytes, list[Pattern]] = {}
        self.global_patterns = []
        excl = repo.gitdir / "info" / "exclude"
        if excl.is_file():
            self.global_patterns.append(parse_patterns(excl.read_bytes(), b"", ".git/info/exclude"))
        cfg = repo.config.get("core.excludesFile")
        if cfg:
            ef = Path(os.path.expanduser(cfg))
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
                os.environ.get("HOME") or os.path.expanduser("~"), ".config")
            ef = Path(xdg) / "git" / "ignore"
        if ef.is_file():
            self.global_patterns.append(parse_patterns(ef.read_bytes(), b"", str(ef).replace("\\", "/")))
        # Lowest priority comes last; search order is reversed below.
        self.global_patterns.reverse()
        self.cli_patterns = parse_patterns(b"\n".join(extra_patterns), b"", "<command line>") \
            if extra_patterns else []

    def patterns_for_dir(self, d: bytes) -> list[Pattern]:
        """Patterns from `<d>/.gitignore` (d is "" for the root, else "a/b")."""
        if d not in self._dir_patterns:
            f = self.root / os.fsdecode(d) / ".gitignore" if d else self.root / ".gitignore"
            pats = []
            try:
                if f.is_file():
                    base = d + b"/" if d else b""
                    pats = parse_patterns(f.read_bytes(), base, (d.decode(errors="replace") + "/.gitignore") if d else ".gitignore")
            except OSError:
                pats = []
            self._dir_patterns[d] = pats
        return self._dir_patterns[d]

    def match(self, path: bytes, is_dir: bool) -> Pattern | None:
        """The pattern that decides `path` (negated or not), or None."""
        if self.cli_patterns:
            for p in reversed(self.cli_patterns):
                if p.matches(path, is_dir, self.icase):
                    return p
        parts = path.split(b"/")
        for depth in range(len(parts) - 1, -1, -1):
            d = b"/".join(parts[:depth])
            for p in reversed(self.patterns_for_dir(d)):
                if p.matches(path, is_dir, self.icase):
                    return p
        for group in self.global_patterns:
            for p in reversed(group):
                if p.matches(path, is_dir, self.icase):
                    return p
        return None

    def is_ignored_here(self, path: bytes, is_dir: bool) -> bool:
        """Ignored by its own patterns, without looking at parent dirs."""
        p = self.match(path, is_dir)
        return p is not None and not p.negated

    def excluding_pattern(self, path: bytes, is_dir: bool) -> Pattern | None:
        """The pattern that excludes `path`, counting excluded parent dirs."""
        parts = path.split(b"/")
        for i in range(1, len(parts)):
            parent = b"/".join(parts[:i])
            p = self.match(parent, True)
            if p is not None and not p.negated:
                return p
        p = self.match(path, is_dir)
        return p if p is not None and not p.negated else None

    def is_ignored(self, path: bytes, is_dir: bool) -> bool:
        return self.excluding_pattern(path, is_dir) is not None

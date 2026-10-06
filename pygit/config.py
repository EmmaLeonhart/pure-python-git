"""Reading and writing git config files (the INI-like format git uses).

Supports sections, `[section "subsection"]`, the deprecated `[section.sub]`
form, quoted values with escapes, `#`/`;` comments, line continuations and
boolean keys without a value. Includes (`[include]`) are not followed.
Lookups use git's precedence: system < global < repository.
"""

from __future__ import annotations

import os
from pathlib import Path


class ConfigError(Exception):
    pass


def _parse_value(raw: str) -> str:
    out = []
    in_quote = False
    i = 0
    pending_space = ""
    while i < len(raw):
        c = raw[i]
        if not in_quote and c in "#;":
            break
        if c == '"':
            in_quote = not in_quote
            out.append(pending_space)
            pending_space = ""
        elif c == "\\":
            i += 1
            if i >= len(raw):
                break
            e = raw[i]
            out.append(pending_space)
            pending_space = ""
            out.append({"n": "\n", "t": "\t", "b": "\b", '"': '"', "\\": "\\"}.get(e, e))
        elif c in " \t" and not in_quote:
            if out or pending_space:
                pending_space += c
        else:
            out.append(pending_space)
            pending_space = ""
            out.append(c)
        i += 1
    return "".join(out)


def parse(text: str) -> list[tuple[str, str | None, str, str | None]]:
    """Parse config text into (section, subsection, key, value) entries.

    Section and key names are lowercased; subsections keep their case.
    A key with no `=` has value None (git treats it as boolean true).
    """
    entries = []
    section = None
    subsection = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        # Join continuation lines (a backslash at the very end).
        while line.endswith("\\") and not line.endswith("\\\\") and i < len(lines):
            line = line[:-1] + lines[i]
            i += 1
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        if s.startswith("["):
            end = s.find("]")
            if end < 0:
                raise ConfigError(f"bad config line: {line!r}")
            header = s[1:end].strip()
            rest = s[end + 1:].strip()
            if '"' in header:
                name, _, sub = header.partition(" ")
                sub = sub.strip()
                if not (sub.startswith('"') and sub.endswith('"')):
                    raise ConfigError(f"bad section header: {line!r}")
                sub = sub[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                section, subsection = name.lower(), sub
            elif "." in header:
                name, _, sub = header.partition(".")
                section, subsection = name.lower(), sub.lower()
            else:
                section, subsection = header.lower(), None
            if not rest or rest[0] in "#;":
                continue
            s = rest
        if section is None:
            raise ConfigError(f"key outside a section: {line!r}")
        if "=" in s:
            key, _, raw = s.partition("=")
            entries.append((section, subsection, key.strip().lower(), _parse_value(raw.strip())))
        else:
            key = s.split()[0]
            entries.append((section, subsection, key.lower(), None))
    return entries


def _split_name(name: str) -> tuple[str, str | None, str]:
    parts = name.split(".")
    if len(parts) < 2:
        raise ConfigError(f"key does not contain a section: {name}")
    section = parts[0].lower()
    key = parts[-1].lower()
    sub = ".".join(parts[1:-1]) if len(parts) > 2 else None
    return section, sub, key


def parse_bool(value: str | None) -> bool:
    if value is None:
        return True
    v = value.strip().lower()
    if v in ("true", "yes", "on", "1"):
        return True
    if v in ("false", "no", "off", "0", ""):
        return False
    try:
        return int(v) != 0
    except ValueError:
        raise ConfigError(f"bad boolean config value '{value}'")


def global_config_paths() -> list[Path]:
    paths = []
    if not os.environ.get("GIT_CONFIG_NOSYSTEM"):
        prefix = os.environ.get("PYGIT_SYSTEM_CONFIG")
        if prefix:
            paths.append(Path(prefix))
    xdg = os.environ.get("XDG_CONFIG_HOME")
    home = os.environ.get("HOME") or os.path.expanduser("~")
    paths.append(Path(xdg) / "git" / "config" if xdg else Path(home) / ".config" / "git" / "config")
    gc = os.environ.get("GIT_CONFIG_GLOBAL")
    paths.append(Path(gc) if gc else Path(home) / ".gitconfig")
    return paths


class Config:
    """Merged view of several config files, last file wins."""

    def __init__(self, files: list[Path]):
        self.files = files
        self.entries = []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
                continue
            self.entries.extend(parse(text))

    def get_all(self, name: str) -> list[str | None]:
        section, sub, key = _split_name(name)
        out = []
        for s, ss, k, v in self.entries:
            if s == section and k == key and ss == sub:
                out.append(v)
        return out

    def get(self, name: str, default=None):
        vals = self.get_all(name)
        if not vals:
            return default
        v = vals[-1]
        return "" if v is None else v

    def get_bool(self, name: str, default: bool = False) -> bool:
        vals = self.get_all(name)
        if not vals:
            return default
        return parse_bool(vals[-1])


def _format_value(value: str) -> str:
    needs_quote = value != value.strip() or any(c in value for c in "#;") or value == ""
    v = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{v}"' if needs_quote else v


def _header(line: str):
    """(section, subsection) for a section header line, else None."""
    entries = parse(line.strip() + "\n_x_ = 1")
    return (entries[0][0], entries[0][1]) if entries else None


def set_value(path: Path, name: str, value: str) -> None:
    """Set name=value in one config file, replacing the last existing entry."""
    section, sub, key = _split_name(name)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    newline = f"\t{key} = {_format_value(value)}"
    cur = None
    key_line = None
    section_last = None
    for idx, line in enumerate(lines):
        s = line.strip()
        if s.startswith("["):
            cur = _header(s)
            if cur == (section, sub):
                section_last = idx
            continue
        if cur == (section, sub):
            section_last = idx
            if s and s[0] not in "#;" and s.partition("=")[0].strip().lower() == key:
                key_line = idx
    if key_line is not None:
        lines[key_line] = newline
    elif section_last is not None:
        lines.insert(section_last + 1, newline)
    else:
        lines.append(f'[{section} "{sub}"]' if sub is not None else f"[{section}]")
        lines.append(newline)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")



"""References: loose refs, `packed-refs`, symbolic refs and HEAD.

Writes go through `<ref>.lock` files renamed into place, as git does, so a
concurrent git never sees a half-written ref. Reflogs are appended when
`core.logAllRefUpdates` is on (the default in non-bare repositories).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from pygit.errors import GitError
from pygit.objects import HEX_RE, ZERO_ID

SYMREF_PREFIX = "ref: "
MAX_SYMREF_DEPTH = 5

_BAD_REF = re.compile(r"(^|/)\.|\.\.|[\x00-\x20\x7f~^:?*\[\\]|@\{|\.lock($|/)|/$|^/|//|\.$")


def check_ref_format(name: str) -> bool:
    """A subset of git's check-ref-format rules."""
    return bool(name) and name != "@" and not _BAD_REF.search(name)


class Refs:
    def __init__(self, repo):
        self.repo = repo
        self.gitdir = repo.gitdir

    # -- low level -----------------------------------------------------------------
    def _loose_path(self, name: str) -> Path:
        return self.gitdir / name

    def packed(self) -> dict[str, str]:
        """refname -> oid from packed-refs (peeled lines are skipped)."""
        out = {}
        try:
            text = (self.gitdir / "packed-refs").read_text(encoding="utf-8")
        except FileNotFoundError:
            return out
        for line in text.splitlines():
            if not line or line[0] in "#^":
                continue
            oid, _, name = line.partition(" ")
            out[name] = oid
        return out

    def read_raw(self, name: str) -> str | None:
        """The ref's contents: an oid or `ref: <target>`, or None."""
        p = self._loose_path(name)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        if name == "HEAD" or name.startswith("refs/"):
            return self.packed().get(name)
        return None

    def resolve(self, name: str) -> tuple[str | None, str]:
        """Follow symbolic refs. Returns (oid or None if unborn, final name)."""
        for _ in range(MAX_SYMREF_DEPTH):
            raw = self.read_raw(name)
            if raw is None:
                return None, name
            if raw.startswith(SYMREF_PREFIX):
                name = raw[len(SYMREF_PREFIX):].strip()
                continue
            if not HEX_RE.match(raw):
                raise GitError(f"bad ref {name}: {raw!r}")
            return raw, name
        raise GitError(f"symbolic ref loop at {name}")

    def exists(self, name: str) -> bool:
        return self.read_raw(name) is not None

    def symbolic_target(self, name: str) -> str | None:
        raw = self.read_raw(name)
        if raw and raw.startswith(SYMREF_PREFIX):
            return raw[len(SYMREF_PREFIX):].strip()
        return None

    def head_branch(self) -> str | None:
        """`refs/heads/<branch>` that HEAD points at, or None if detached."""
        return self.symbolic_target("HEAD")

    def list(self, prefix: str = "refs/") -> dict[str, str]:
        """Every ref under `prefix` that resolves to an object: name -> oid."""
        names = set(n for n in self.packed() if n.startswith(prefix))
        base = self.gitdir / prefix.rstrip("/")
        root = base if base.is_dir() else None
        if root is not None:
            for dirpath, _, files in os.walk(root):
                for f in files:
                    if f.endswith(".lock"):
                        continue
                    full = Path(dirpath) / f
                    names.add(full.relative_to(self.gitdir).as_posix())
        out = {}
        for n in sorted(names, key=lambda s: s.encode()):
            try:
                oid, _ = self.resolve(n)
            except GitError:
                continue
            if oid:
                out[n] = oid
        return out

    # -- writing -------------------------------------------------------------------
    def _write_locked(self, name: str, content: str) -> None:
        path = self._loose_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_name(path.name + ".lock")
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise GitError(f"Unable to create '{lock}': File exists.")
        try:
            os.write(fd, content.encode())
        finally:
            os.close(fd)
        os.replace(lock, path)

    def _log(self, name: str, old: str, new: str, message: str) -> None:
        cfg = self.repo.config
        default = not self.repo.bare
        if not cfg.get_bool("core.logAllRefUpdates", default):
            return
        if not (name == "HEAD" or name.startswith(("refs/heads/", "refs/remotes/", "refs/notes/"))):
            if not (self.gitdir / "logs" / name).exists():
                return
        from pygit.ident import ident
        sig = ident(self.repo, "committer")
        line = f"{old} {new} ".encode() + sig.serialize() + b"\t" + message.encode() + b"\n"
        p = self.gitdir / "logs" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as f:
            f.write(line)

    def update(self, name: str, oid: str, message: str = "", old: str | None = None,
               no_deref: bool = False) -> None:
        """Point `name` at `oid`, following symbolic refs unless `no_deref`.

        `old`, when given, must match the current value (ZERO_ID = must not exist).
        """
        target = name
        if not no_deref:
            cur, target = self.resolve(name)
        else:
            raw = self.read_raw(name)
            cur = raw if raw and not raw.startswith(SYMREF_PREFIX) else None
        if old is not None and (cur or ZERO_ID) != old:
            raise GitError(f"cannot lock ref '{name}': is at {cur or ZERO_ID} but expected {old}")
        self._write_locked(target, oid + "\n")
        self._log(target, cur or ZERO_ID, oid, message)
        if target != name and name == "HEAD":
            self._log("HEAD", cur or ZERO_ID, oid, message)

    def set_symbolic(self, name: str, target: str, message: str = "") -> None:
        old, _ = self.resolve(name) if self.exists(name) else (None, name)
        self._write_locked(name, f"{SYMREF_PREFIX}{target}\n")
        if message:
            new, _ = self.resolve(name)
            if new:
                self._log(name, old or ZERO_ID, new, message)

    def delete(self, name: str) -> None:
        p = self._loose_path(name)
        if p.is_file():
            p.unlink()
            # Remove empty parent directories up to refs/<kind>/.
            parent = p.parent
            while parent != self.gitdir and parent.name not in ("heads", "tags", "remotes", "refs"):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        packed = self.gitdir / "packed-refs"
        if packed.is_file():
            lines = packed.read_text(encoding="utf-8").splitlines()
            out = []
            skip_peel = False
            for line in lines:
                if line.startswith("^"):
                    if not skip_peel:
                        out.append(line)
                    continue
                skip_peel = line.endswith(" " + name)
                if not skip_peel:
                    out.append(line)
            if out != lines:
                tmp = packed.with_name("packed-refs.lock")
                tmp.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
                os.replace(tmp, packed)
        log = self.gitdir / "logs" / name
        if log.is_file():
            log.unlink()

    def write_packed(self, refs: dict[str, str], peeled: dict[str, str]) -> None:
        """Rewrite packed-refs with `refs` (name -> oid), sorted, with peel lines."""
        lines = ["# pack-refs with: peeled fully-peeled sorted "]
        for name in sorted(refs, key=lambda s: s.encode()):
            lines.append(f"{refs[name]} {name}")
            if name in peeled:
                lines.append(f"^{peeled[name]}")
        tmp = self.gitdir / "packed-refs.lock"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, self.gitdir / "packed-refs")


def now_signature_time() -> tuple[int, str]:
    """Current time and local UTC offset as git formats it (+HHMM)."""
    t = int(time.time())
    lt = time.localtime(t)
    off = lt.tm_gmtoff // 60
    sign = "-" if off < 0 else "+"
    off = abs(off)
    return t, f"{sign}{off // 60:02d}{off % 60:02d}"

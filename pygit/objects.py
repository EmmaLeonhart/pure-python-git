"""The object store: hashing, loose objects, and the four object types.

An object is stored as `<type> <size>\\0<content>`, zlib-compressed, at
`objects/<first 2 hex>/<remaining 38 hex>`; its id is the SHA-1 of the
uncompressed bytes. Packed objects are read through `pygit.pack` once that
stage exists; `ObjectStore` is the single entry point for both.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from pygit.errors import GitError

TYPES = (b"blob", b"tree", b"commit", b"tag")
HEX_RE = re.compile(r"^[0-9a-f]{40}$")
ZERO_ID = "0" * 40


def header(obj_type: bytes, size: int) -> bytes:
    return obj_type + b" " + str(size).encode() + b"\0"


def hash_bytes(obj_type: bytes, data: bytes) -> str:
    return hashlib.sha1(header(obj_type, len(data)) + data).hexdigest()


class ObjectStore:
    """Loose objects under `objects/`, plus packs when present."""

    def __init__(self, objects_dir: Path):
        self.dir = Path(objects_dir)
        self._packs = None

    def loose_path(self, oid: str) -> Path:
        return self.dir / oid[:2] / oid[2:]

    # -- packs (filled in by pygit.pack) ------------------------------------
    @property
    def packs(self):
        if self._packs is None:
            try:
                from pygit import pack
            except ImportError:
                self._packs = []
            else:
                self._packs = pack.load_packs(self.dir / "pack")
        return self._packs

    def refresh_packs(self) -> None:
        self._packs = None

    # -- reading ---------------------------------------------------------------
    def read_loose(self, oid: str) -> tuple[bytes, bytes] | None:
        try:
            raw = zlib.decompress(self.loose_path(oid).read_bytes())
        except FileNotFoundError:
            return None
        nul = raw.index(b"\0")
        obj_type, _, size = raw[:nul].partition(b" ")
        data = raw[nul + 1:]
        if int(size) != len(data):
            raise GitError(f"object {oid} is corrupt (size mismatch)")
        return obj_type, data

    def read(self, oid: str) -> tuple[bytes, bytes]:
        """Return (type, content). Raises GitError if missing."""
        got = self.read_loose(oid)
        if got is not None:
            return got
        for p in self.packs:
            got = p.read(oid)
            if got is not None:
                return got
        raise GitError(f"object {oid} not found")

    def exists(self, oid: str) -> bool:
        if self.loose_path(oid).is_file():
            return True
        return any(p.contains(oid) for p in self.packs)

    def all_ids(self):
        """Every object id in the store (loose and packed)."""
        seen = set()
        if self.dir.is_dir():
            for d in self.dir.iterdir():
                if len(d.name) == 2 and d.is_dir():
                    for f in d.iterdir():
                        if len(f.name) == 38:
                            seen.add(d.name + f.name)
        for p in self.packs:
            seen.update(p.ids())
        return seen

    def find_prefix(self, prefix: str) -> list[str]:
        """All ids starting with the hex `prefix` (at least 2 characters)."""
        out = set()
        d = self.dir / prefix[:2]
        if d.is_dir():
            rest = prefix[2:]
            for f in d.iterdir():
                if len(f.name) == 38 and f.name.startswith(rest):
                    out.add(prefix[:2] + f.name)
        for p in self.packs:
            out.update(p.find_prefix(prefix))
        return sorted(out)

    # -- writing -----------------------------------------------------------------
    def write(self, obj_type: bytes, data: bytes) -> str:
        oid = hash_bytes(obj_type, data)
        if self.exists(oid):
            return oid
        path = self.loose_path(oid)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="tmp_obj_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(zlib.compress(header(obj_type, len(data)) + data, 1))
            os.chmod(tmp, 0o444)
            try:
                os.replace(tmp, path)
            except PermissionError:
                # Another writer won the race; the object is identical.
                if not path.exists():
                    raise
                os.unlink(tmp)
        except BaseException:
            if os.path.exists(tmp):
                os.chmod(tmp, 0o644)
                os.unlink(tmp)
            raise
        return oid


# -- trees --------------------------------------------------------------------

@dataclass
class TreeEntry:
    mode: int          # e.g. 0o100644, 0o100755, 0o120000, 0o40000, 0o160000
    name: bytes
    oid: str

    @property
    def type(self) -> bytes:
        if self.mode == 0o40000:
            return b"tree"
        if self.mode == 0o160000:
            return b"commit"
        return b"blob"

    @property
    def is_tree(self) -> bool:
        return self.mode == 0o40000


def parse_tree(data: bytes) -> list[TreeEntry]:
    entries = []
    i = 0
    while i < len(data):
        sp = data.index(b" ", i)
        nul = data.index(b"\0", sp)
        mode = int(data[i:sp], 8)
        name = data[sp + 1:nul]
        oid = data[nul + 1:nul + 21].hex()
        entries.append(TreeEntry(mode, name, oid))
        i = nul + 21
    return entries


def tree_sort_key(e: TreeEntry) -> bytes:
    # git sorts tree entries as if directories had a trailing '/'.
    return e.name + b"/" if e.is_tree else e.name


def serialize_tree(entries: list[TreeEntry]) -> bytes:
    out = []
    for e in sorted(entries, key=tree_sort_key):
        out.append(b"%o %s\0" % (e.mode, e.name) + bytes.fromhex(e.oid))
    return b"".join(out)


# -- commits and tags ------------------------------------------------------------

@dataclass
class Signature:
    name: bytes
    email: bytes
    timestamp: int
    tz: bytes  # e.g. b"+0200"

    @classmethod
    def parse(cls, raw: bytes) -> "Signature":
        lt = raw.index(b"<")
        gt = raw.index(b">", lt)
        name = raw[:lt].rstrip(b" ")
        email = raw[lt + 1:gt]
        rest = raw[gt + 1:].split()
        ts = int(rest[0]) if rest else 0
        tz = rest[1] if len(rest) > 1 else b"+0000"
        return cls(name, email, ts, tz)

    def serialize(self) -> bytes:
        return b"%s <%s> %d %s" % (self.name, self.email, self.timestamp, self.tz)

    @property
    def tz_offset_minutes(self) -> int:
        sign = -1 if self.tz.startswith(b"-") else 1
        digits = self.tz.lstrip(b"+-")
        return sign * (int(digits[:2]) * 60 + int(digits[2:4]))


def parse_headers(data: bytes) -> tuple[list[tuple[bytes, bytes]], bytes]:
    """Split a commit or tag into ordered (key, value) headers and the message.

    Continuation lines (starting with a space, as in `gpgsig`) are joined to
    the previous value with newlines.
    """
    headers: list[tuple[bytes, bytes]] = []
    pos = 0
    while pos < len(data):
        end = data.find(b"\n", pos)
        if end < 0:
            end = len(data)
        line = data[pos:end]
        pos = end + 1
        if line == b"":
            return headers, data[pos:]
        if line.startswith(b" ") and headers:
            k, v = headers[-1]
            headers[-1] = (k, v + b"\n" + line[1:])
            continue
        k, _, v = line.partition(b" ")
        headers.append((k, v))
    return headers, b""


def serialize_headers(headers: list[tuple[bytes, bytes]], message: bytes) -> bytes:
    out = []
    for k, v in headers:
        out.append(k + b" " + v.replace(b"\n", b"\n ") + b"\n")
    return b"".join(out) + b"\n" + message


@dataclass
class Commit:
    tree: str
    parents: list[str]
    author: Signature
    committer: Signature
    message: bytes
    extra: list[tuple[bytes, bytes]] = field(default_factory=list)

    @classmethod
    def parse(cls, data: bytes) -> "Commit":
        headers, message = parse_headers(data)
        tree = None
        parents = []
        author = committer = None
        extra = []
        for k, v in headers:
            if k == b"tree" and tree is None:
                tree = v.decode()
            elif k == b"parent":
                parents.append(v.decode())
            elif k == b"author" and author is None:
                author = Signature.parse(v)
            elif k == b"committer" and committer is None:
                committer = Signature.parse(v)
            else:
                extra.append((k, v))
        if tree is None:
            raise GitError("commit object has no tree")
        return cls(tree, parents, author, committer, message, extra)

    def serialize(self) -> bytes:
        headers = [(b"tree", self.tree.encode())]
        headers += [(b"parent", p.encode()) for p in self.parents]
        headers.append((b"author", self.author.serialize()))
        headers.append((b"committer", self.committer.serialize()))
        headers += self.extra
        return serialize_headers(headers, self.message)


@dataclass
class Tag:
    object: str
    type: bytes
    tag: bytes
    tagger: Signature | None
    message: bytes
    extra: list[tuple[bytes, bytes]] = field(default_factory=list)

    @classmethod
    def parse(cls, data: bytes) -> "Tag":
        headers, message = parse_headers(data)
        fields = {}
        extra = []
        for k, v in headers:
            if k in (b"object", b"type", b"tag", b"tagger") and k not in fields:
                fields[k] = v
            else:
                extra.append((k, v))
        return cls(fields[b"object"].decode(), fields[b"type"], fields[b"tag"],
                   Signature.parse(fields[b"tagger"]) if b"tagger" in fields else None,
                   message, extra)

    def serialize(self) -> bytes:
        headers = [(b"object", self.object.encode()), (b"type", self.type), (b"tag", self.tag)]
        if self.tagger is not None:
            headers.append((b"tagger", self.tagger.serialize()))
        headers += self.extra
        return serialize_headers(headers, self.message)


def format_tree_line(e: TreeEntry, path: bytes | None = None, quote: bool = True) -> bytes:
    """One `ls-tree`/`cat-file -p` line: `<mode> <type> <oid>\\t<path>`."""
    from pygit.quote import quote_path
    name = path if path is not None else e.name
    shown = quote_path(name) if quote else name
    return b"%06o %s %s\t%s" % (e.mode, e.type, e.oid.encode(), shown)

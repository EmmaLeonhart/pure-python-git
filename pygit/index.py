"""The index (`.git/index`): git's staging area, in its binary format.

Layout (version 2; versions 3 and 4 are read too, version 2 is written):

    "DIRC" | version (u32) | entry count (u32)
    entries, sorted by (path, stage):
        ctime s/ns, mtime s/ns, dev, ino, mode, uid, gid, size  (u32 each)
        object id (20 bytes) | flags (u16) [| extended flags (u16), v3+]
        path, NUL-padded so the entry length is a multiple of 8
    extensions: 4-byte signature | u32 size | data
    SHA-1 of everything above

Extensions are skipped on read and not written: git rebuilds its cache-tree
(`TREE`) and other optional data when it needs them.
"""

from __future__ import annotations

import hashlib
import os
import stat as statmod
import struct
from dataclasses import dataclass
from pathlib import Path

from pygit.errors import GitError
from pygit.objects import TreeEntry, serialize_tree

_ENTRY_HEAD = struct.Struct(">10I20sH")
FLAG_ASSUME_VALID = 0x8000
FLAG_EXTENDED = 0x4000
FLAG_STAGE_MASK = 0x3000
NAME_MASK = 0x0FFF
EXT_SKIP_WORKTREE = 0x4000
EXT_INTENT_TO_ADD = 0x2000


@dataclass
class IndexEntry:
    path: bytes
    oid: str
    mode: int
    size: int = 0
    ctime: tuple = (0, 0)
    mtime: tuple = (0, 0)
    dev: int = 0
    ino: int = 0
    uid: int = 0
    gid: int = 0
    stage: int = 0
    assume_valid: bool = False
    ext_flags: int = 0

    def set_stat(self, st: os.stat_result) -> None:
        self.ctime = (int(st.st_ctime) & 0xFFFFFFFF, st.st_ctime_ns % 1_000_000_000)
        self.mtime = (int(st.st_mtime) & 0xFFFFFFFF, st.st_mtime_ns % 1_000_000_000)
        if os.name == "nt":
            # Git for Windows' lstat reports these as 0, and it compares them.
            self.dev = self.ino = self.uid = self.gid = 0
        else:
            self.dev = st.st_dev & 0xFFFFFFFF
            self.ino = st.st_ino & 0xFFFFFFFF
            self.uid = st.st_uid & 0xFFFFFFFF
            self.gid = st.st_gid & 0xFFFFFFFF
        self.size = st.st_size & 0xFFFFFFFF

    def stat_matches(self, st: os.stat_result, trust_ctime: bool = True) -> bool:
        """True if the file looks unchanged since this entry was recorded.

        Like git's ie_match_stat this compares mtime, size and (optionally)
        ctime, inode and device; a mismatch only means "check the content".
        """
        if self.mtime != (int(st.st_mtime) & 0xFFFFFFFF, st.st_mtime_ns % 1_000_000_000):
            return False
        if self.size != st.st_size & 0xFFFFFFFF:
            return False
        if trust_ctime and os.name != "nt" and \
                self.ctime != (int(st.st_ctime) & 0xFFFFFFFF, st.st_ctime_ns % 1_000_000_000):
            return False
        return True


def mode_from_stat(st: os.stat_result, filemode: bool = True, old_mode: int | None = None) -> int:
    if statmod.S_ISLNK(st.st_mode):
        return 0o120000
    if statmod.S_ISDIR(st.st_mode):
        return 0o160000
    if not filemode:
        # core.fileMode=false: keep what the index says, else a plain file.
        return old_mode if old_mode in (0o100644, 0o100755) else 0o100644
    return 0o100755 if st.st_mode & 0o100 else 0o100644


def entry_sort_key(e: IndexEntry):
    return (e.path, e.stage)


class Index:
    def __init__(self, path: Path, entries: list[IndexEntry] | None = None, version: int = 2):
        self.path = Path(path)
        self.entries: dict[tuple[bytes, int], IndexEntry] = {}
        self.version = version
        for e in entries or []:
            self.entries[(e.path, e.stage)] = e

    # -- reading -------------------------------------------------------------------
    @classmethod
    def read(cls, repo) -> "Index":
        path = Path(os.environ.get("GIT_INDEX_FILE") or repo.gitdir / "index")
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return cls(path)
        return cls.parse(path, data)

    @classmethod
    def parse(cls, path: Path, data: bytes) -> "Index":
        if len(data) < 32 or data[:4] != b"DIRC":
            raise GitError("index file corrupt (bad signature)")
        if hashlib.sha1(data[:-20]).digest() != data[-20:]:
            raise GitError("index file corrupt (bad checksum)")
        version, count = struct.unpack(">II", data[4:12])
        if version not in (2, 3, 4):
            raise GitError(f"index file has unsupported version {version}")
        pos = 12
        entries = []
        prev_path = b""
        for _ in range(count):
            fields = _ENTRY_HEAD.unpack_from(data, pos)
            ctime_s, ctime_ns, mtime_s, mtime_ns, dev, ino, mode, uid, gid, size, sha, flags = fields
            pos += _ENTRY_HEAD.size
            ext = 0
            if flags & FLAG_EXTENDED:
                ext, = struct.unpack_from(">H", data, pos)
                pos += 2
            if version == 4:
                # Prefix-compressed path: varint "strip N bytes" + suffix\0.
                strip, n = _read_varint(data, pos)
                pos = n
                nul = data.index(b"\0", pos)
                name = prev_path[:len(prev_path) - strip] + data[pos:nul]
                pos = nul + 1
            else:
                nul = data.index(b"\0", pos)
                name = data[pos:nul]
                entry_len = _ENTRY_HEAD.size + (2 if flags & FLAG_EXTENDED else 0) + len(name)
                pos += len(name) + (8 - entry_len % 8)
            prev_path = name
            entries.append(IndexEntry(
                path=name, oid=sha.hex(), mode=mode, size=size,
                ctime=(ctime_s, ctime_ns), mtime=(mtime_s, mtime_ns),
                dev=dev, ino=ino, uid=uid, gid=gid,
                stage=(flags & FLAG_STAGE_MASK) >> 12,
                assume_valid=bool(flags & FLAG_ASSUME_VALID), ext_flags=ext))
        return cls(path, entries, version)

    # -- writing -------------------------------------------------------------------
    def serialize(self) -> bytes:
        entries = self.sorted_entries()
        version = 3 if any(e.ext_flags for e in entries) else 2
        out = [b"DIRC", struct.pack(">II", version, len(entries))]
        for e in entries:
            flags = (min(len(e.path), NAME_MASK)) | (e.stage << 12)
            if e.assume_valid:
                flags |= FLAG_ASSUME_VALID
            if e.ext_flags:
                flags |= FLAG_EXTENDED
            head = _ENTRY_HEAD.pack(e.ctime[0], e.ctime[1], e.mtime[0], e.mtime[1], e.dev, e.ino,
                                    e.mode, e.uid, e.gid, e.size, bytes.fromhex(e.oid), flags)
            if e.ext_flags:
                head += struct.pack(">H", e.ext_flags)
            entry_len = len(head) + len(e.path)
            out.append(head + e.path + b"\0" * (8 - entry_len % 8))
        body = b"".join(out)
        return body + hashlib.sha1(body).digest()

    def write(self) -> None:
        """Write through `index.lock` and rename, as git does."""
        from pygit.lockfile import write_locked
        lock = self.path.with_name(self.path.name + ".lock")
        write_locked(self.path, self.serialize(),
                     f"Unable to create '{lock}': File exists.\n\n"
                     "Another git process seems to be running in this repository.")

    # -- access --------------------------------------------------------------------
    def sorted_entries(self) -> list[IndexEntry]:
        return sorted(self.entries.values(), key=entry_sort_key)

    def get(self, path: bytes, stage: int = 0) -> IndexEntry | None:
        return self.entries.get((path, stage))

    def paths(self) -> set[bytes]:
        return {p for p, _ in self.entries}

    def add(self, e: IndexEntry) -> None:
        """Insert at stage 0, replacing conflict stages and D/F clashes."""
        for s in (1, 2, 3):
            self.entries.pop((e.path, s), None)
        # A file replaces a directory of the same name and vice versa.
        prefix = e.path + b"/"
        for key in [k for k in self.entries if k[0].startswith(prefix)]:
            del self.entries[key]
        parts = e.path.split(b"/")
        for i in range(1, len(parts)):
            for s in range(4):
                self.entries.pop((b"/".join(parts[:i]), s), None)
        self.entries[(e.path, e.stage)] = e

    def remove(self, path: bytes) -> bool:
        found = False
        for s in range(4):
            if self.entries.pop((path, s), None) is not None:
                found = True
        return found

    def has_conflicts(self) -> bool:
        return any(stage for _, stage in self.entries)

    def conflicted_paths(self) -> list[bytes]:
        return sorted({p for p, s in self.entries if s})

    # -- trees ---------------------------------------------------------------------
    def write_tree(self, odb) -> str:
        """Write the tree objects for stage-0 entries; returns the root tree id."""
        if self.has_conflicts():
            raise GitError("cannot write a tree from an index with unmerged entries")
        root: dict = {}
        for e in self.sorted_entries():
            if e.ext_flags & EXT_INTENT_TO_ADD:
                continue
            parts = e.path.split(b"/")
            node = root
            for d in parts[:-1]:
                node = node.setdefault(d, {})
            node[parts[-1]] = e

        def build(node) -> str:
            entries = []
            for name, v in node.items():
                if isinstance(v, dict):
                    entries.append(TreeEntry(0o40000, name, build(v)))
                else:
                    entries.append(TreeEntry(v.mode, name, v.oid))
            return odb.write(b"tree", serialize_tree(entries))

        return build(root)

    @classmethod
    def from_tree(cls, repo, tree_oid: str | None, path: Path | None = None) -> "Index":
        """An index holding every blob of a tree (stat data zeroed)."""
        idx = cls(path or repo.gitdir / "index")
        if tree_oid:
            for p, mode, oid in walk_tree(repo.odb, tree_oid):
                idx.entries[(p, 0)] = IndexEntry(path=p, oid=oid, mode=mode)
        return idx


def walk_tree(odb, tree_oid: str, prefix: bytes = b""):
    """Yield (path, mode, oid) for every non-tree entry, recursively, in order."""
    from pygit.objects import parse_tree
    for e in parse_tree(odb.read(tree_oid)[1]):
        path = prefix + e.name
        if e.is_tree:
            yield from walk_tree(odb, e.oid, path + b"/")
        else:
            yield path, e.mode, e.oid


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """git's offset varint (used by index v4 and ofs-delta)."""
    b = data[pos]
    pos += 1
    val = b & 0x7F
    while b & 0x80:
        b = data[pos]
        pos += 1
        val = ((val + 1) << 7) | (b & 0x7F)
    return val, pos

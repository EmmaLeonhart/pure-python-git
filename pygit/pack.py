"""Packfiles: reading `.pack`/`.idx` pairs, applying deltas, and writing packs.

Pack format (v2): "PACK" | version u32 | object count u32, then objects,
then the SHA-1 of everything before it. An object starts with a varint
header (type in bits 4-6 of the first byte, size in the low bits, 7 bits
per following byte); OFS_DELTA adds a negative offset to its base, REF_DELTA
the base's id; the data is a zlib stream.

Index (v2): "\\377tOc" | 2 | fanout[256] | ids | CRC32s | 4-byte offsets
(MSB set: index into the 8-byte table) | 8-byte offsets | pack checksum |
index checksum. Version 1 indexes (fanout, then offset+id pairs) are read too.
"""

from __future__ import annotations

import bisect
import hashlib
import os
import struct
import zlib
from collections import OrderedDict
from pathlib import Path

from pygit.errors import GitError

OBJ_COMMIT, OBJ_TREE, OBJ_BLOB, OBJ_TAG, OBJ_OFS_DELTA, OBJ_REF_DELTA = 1, 2, 3, 4, 6, 7
TYPE_NAMES = {OBJ_COMMIT: b"commit", OBJ_TREE: b"tree", OBJ_BLOB: b"blob", OBJ_TAG: b"tag"}
TYPE_NUMS = {v: k for k, v in TYPE_NAMES.items()}
IDX_MAGIC = b"\377tOc"


# -- deltas ---------------------------------------------------------------------------

def _delta_varint(data, pos):
    shift = val = 0
    while True:
        b = data[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return val, pos


def apply_delta(base: bytes, delta: bytes) -> bytes:
    src_size, pos = _delta_varint(delta, 0)
    dst_size, pos = _delta_varint(delta, pos)
    if src_size != len(base):
        raise GitError("delta base size mismatch")
    out = bytearray()
    n = len(delta)
    while pos < n:
        op = delta[pos]
        pos += 1
        if op & 0x80:
            off = size = 0
            for i in range(4):
                if op & (1 << i):
                    off |= delta[pos] << (8 * i)
                    pos += 1
            for i in range(3):
                if op & (0x10 << i):
                    size |= delta[pos] << (8 * i)
                    pos += 1
            if size == 0:
                size = 0x10000
            out += base[off:off + size]
        elif op:
            out += delta[pos:pos + op]
            pos += op
        else:
            raise GitError("corrupt delta: reserved opcode 0")
    if len(out) != dst_size:
        raise GitError("delta result size mismatch")
    return bytes(out)


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


BLOCK = 16


def make_delta(base: bytes, target: bytes, max_size: int | None = None) -> bytes | None:
    """A git-format delta turning base into target, or None if not worth it.

    Base blocks of 16 bytes are indexed by content; the target is scanned
    for matches, which are extended in both directions and emitted as copy
    instructions, with literals as inserts.
    """
    if not base or not target:
        return None
    index: dict[bytes, int] = {}
    for i in range(0, len(base) - BLOCK + 1, BLOCK):
        index.setdefault(base[i:i + BLOCK], i)
    out = bytearray(_encode_varint(len(base)) + _encode_varint(len(target)))
    literal = bytearray()

    def flush_literal():
        nonlocal literal
        for k in range(0, len(literal), 127):
            chunk = literal[k:k + 127]
            out.append(len(chunk))
            out.extend(chunk)
        literal = bytearray()

    def emit_copy(off, size):
        while size:
            n = min(size, 0x10000)
            op = 0x80
            args = bytearray()
            for i in range(4):
                byte = (off >> (8 * i)) & 0xFF
                if byte:
                    op |= 1 << i
                    args.append(byte)
            sz = 0 if n == 0x10000 else n
            for i in range(3):
                byte = (sz >> (8 * i)) & 0xFF
                if byte:
                    op |= 0x10 << i
                    args.append(byte)
            out.append(op)
            out.extend(args)
            off += n
            size -= n

    i = 0
    n = len(target)
    while i < n:
        if i + BLOCK <= n:
            src = index.get(target[i:i + BLOCK])
        else:
            src = None
        if src is None:
            literal.append(target[i])
            i += 1
            continue
        # Extend backwards into the pending literal, then forwards.
        back = 0
        while back < len(literal) and src - back > 0 and base[src - back - 1] == literal[-1 - back]:
            back += 1
        if back:
            del literal[len(literal) - back:]
        start_t, start_s = i - back, src - back
        length = BLOCK + back
        while i - back + length < n and start_s + length < len(base) and \
                target[start_t + length] == base[start_s + length]:
            length += 1
        flush_literal()
        emit_copy(start_s, length)
        i = start_t + length
        if max_size is not None and len(out) > max_size:
            return None
    flush_literal()
    if max_size is not None and len(out) > max_size:
        return None
    return bytes(out)


# -- reading ---------------------------------------------------------------------------

class Pack:
    def __init__(self, idx_path: Path):
        self.idx_path = Path(idx_path)
        self.pack_path = self.idx_path.with_suffix(".pack")
        self._idx = self.idx_path.read_bytes()
        self._data = None
        self._parse_index()
        self._cache: OrderedDict = OrderedDict()

    # index
    def _parse_index(self):
        d = self._idx
        if d[:4] == IDX_MAGIC:
            version = struct.unpack(">I", d[4:8])[0]
            if version != 2:
                raise GitError(f"unsupported pack index version {version}")
            fan = struct.unpack(">256I", d[8:8 + 1024])
            n = fan[255]
            pos = 8 + 1024
            self.oids = [d[pos + 20 * i:pos + 20 * i + 20].hex() for i in range(n)]
            pos += 20 * n
            self.crcs = struct.unpack(f">{n}I", d[pos:pos + 4 * n])
            pos += 4 * n
            off32 = struct.unpack(f">{n}I", d[pos:pos + 4 * n])
            pos += 4 * n
            large_count = sum(1 for o in off32 if o & 0x80000000)
            off64 = struct.unpack(f">{large_count}Q", d[pos:pos + 8 * large_count])
            self.offsets = [off64[o & 0x7FFFFFFF] if o & 0x80000000 else o for o in off32]
            self.version = 2
        else:
            fan = struct.unpack(">256I", d[:1024])
            n = fan[255]
            pos = 1024
            self.oids, self.offsets = [], []
            for i in range(n):
                off, = struct.unpack(">I", d[pos:pos + 4])
                self.oids.append(d[pos + 4:pos + 24].hex())
                self.offsets.append(off)
                pos += 24
            self.crcs = None
            self.version = 1
        self._pos = {oid: i for i, oid in enumerate(self.oids)}
        self.by_offset = sorted(range(len(self.oids)), key=lambda i: self.offsets[i])
        self._rank = {i: r for r, i in enumerate(self.by_offset)}

    @property
    def data(self) -> bytes:
        if self._data is None:
            self._data = self.pack_path.read_bytes()
            if self._data[:4] != b"PACK":
                raise GitError(f"{self.pack_path} is not a pack file")
        return self._data

    def contains(self, oid: str) -> bool:
        return oid in self._pos

    def ids(self) -> list[str]:
        """Every object id in the pack, sorted."""
        return list(self.oids)

    def find_prefix(self, prefix: str) -> list[str]:
        i = bisect.bisect_left(self.oids, prefix)
        out = []
        while i < len(self.oids) and self.oids[i].startswith(prefix):
            out.append(self.oids[i])
            i += 1
        return out

    # objects
    def _header(self, pos: int):
        d = self.data
        b = d[pos]
        pos += 1
        typ = (b >> 4) & 7
        size = b & 0x0F
        shift = 4
        while b & 0x80:
            b = d[pos]
            pos += 1
            size |= (b & 0x7F) << shift
            shift += 7
        base = None
        if typ == OBJ_OFS_DELTA:
            b = d[pos]
            pos += 1
            off = b & 0x7F
            while b & 0x80:
                b = d[pos]
                pos += 1
                off = ((off + 1) << 7) | (b & 0x7F)
            base = ("ofs", off)
        elif typ == OBJ_REF_DELTA:
            base = ("ref", d[pos:pos + 20].hex())
            pos += 20
        return typ, size, base, pos

    def _inflate(self, pos: int, size: int) -> tuple[bytes, int]:
        """Decompress the zlib stream at pos; returns (data, end position)."""
        d = self.data
        z = zlib.decompressobj()
        mv = memoryview(d)
        chunk = max(64, size + 64)
        out = bytearray()
        p = pos
        while not z.eof:
            if p >= len(d):
                raise GitError("pack truncated")
            piece = mv[p:p + chunk]
            out += z.decompress(piece)
            p += len(piece) - len(z.unused_data)
            chunk *= 2
        if len(out) != size:
            raise GitError("pack object size mismatch")
        return bytes(out), p

    def read_at(self, offset: int) -> tuple[int, bytes]:
        """(final type number, content) of the object at a pack offset."""
        hit = self._cache.get(offset)
        if hit is not None:
            self._cache.move_to_end(offset)
            return hit
        chain = []
        pos = offset
        while True:
            typ, size, base, dpos = self._header(pos)
            if base is None:
                data, _ = self._inflate(dpos, size)
                break
            delta, _ = self._inflate(dpos, size)
            chain.append(delta)
            if base[0] == "ofs":
                pos = pos - base[1]
                cached = self._cache.get(pos)
                if cached is not None:
                    typ, data = cached
                    break
            else:
                i = self._pos.get(base[1])
                if i is None:
                    raise GitError(f"REF_DELTA base {base[1]} is not in this pack")
                pos = self.offsets[i]
        for delta in reversed(chain):
            data = apply_delta(data, delta)
        result = (typ, data)
        self._cache[offset] = result
        while len(self._cache) > 256:
            self._cache.popitem(last=False)
        return result

    def read(self, oid: str):
        i = self._pos.get(oid)
        if i is None:
            return None
        typ, data = self.read_at(self.offsets[i])
        return TYPE_NAMES[typ], data

    def entry_info(self, i: int):
        """(type, size, packed size, offset, base offset or id) for verify-pack."""
        offset = self.offsets[i]
        rank = self._rank[i]
        nxt = self.offsets[self.by_offset[rank + 1]] if rank + 1 < len(self.by_offset) else len(self.data) - 20
        typ, size, base, _ = self._header(offset)
        return typ, size, nxt - offset, offset, base


def load_packs(pack_dir: Path) -> list[Pack]:
    packs = []
    try:
        names = sorted(os.listdir(pack_dir))
    except FileNotFoundError:
        return packs
    for name in names:
        if name.endswith(".idx") and (Path(pack_dir) / (name[:-4] + ".pack")).is_file():
            packs.append(Pack(Path(pack_dir) / name))
    # Newest first, as git prefers recently written packs.
    packs.sort(key=lambda p: -p.idx_path.stat().st_mtime)
    return packs


# -- writing ----------------------------------------------------------------------------

DELTA_WINDOW = 10
DELTA_DEPTH = 50


def name_hash(name: bytes) -> int:
    """git's pack_name_hash: groups files by the end of their name."""
    h = 0
    for c in name:
        if c in b" \t\n\r":
            continue
        h = ((h >> 2) + (c << 24)) & 0xFFFFFFFF
    return h


def _obj_header(typ: int, size: int) -> bytes:
    b = (typ << 4) | (size & 0x0F)
    size >>= 4
    out = bytearray()
    while size:
        out.append(b | 0x80)
        b = size & 0x7F
        size >>= 7
    out.append(b)
    return bytes(out)


def _ofs_encoding(n: int) -> bytes:
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        n -= 1
        out.insert(0, 0x80 | (n & 0x7F))
        n >>= 7
    return bytes(out)


def write_pack(odb, objects: list[tuple[str, bytes | None]], pack_dir: Path, deltas: bool = True,
               base_name: str | None = None) -> tuple[Path, str]:
    """Write `objects` ([(oid, path name or None)], in the desired order) into
    a new pack plus .idx. Returns (pack path, pack checksum hex)."""
    pack_dir = Path(pack_dir)
    pack_dir.mkdir(parents=True, exist_ok=True)
    infos = {}
    order = []
    for oid, name in objects:
        if oid in infos:
            continue
        t, data = odb.read(oid)
        infos[oid] = {"type": TYPE_NUMS[t], "data": data, "name": name or b"", "base": None,
                      "delta": None, "depth": 0}
        order.append(oid)
    if deltas:
        cands = sorted((o for o in order if infos[o]["type"] in (OBJ_BLOB, OBJ_TREE)),
                       key=lambda o: (infos[o]["type"], name_hash(infos[o]["name"]), -len(infos[o]["data"])))
        window = []
        for oid in cands:
            info = infos[oid]
            best = None
            data = info["data"]
            if len(data) >= 32:
                for other in window:
                    oi = infos[other]
                    if oi["type"] != info["type"] or oi["depth"] >= DELTA_DEPTH:
                        continue
                    limit = (len(data) // 2 - 20) if best is None else len(best[1]) - 1
                    if limit <= 0:
                        continue
                    d = make_delta(oi["data"], data, max_size=limit)
                    if d is not None and (best is None or len(d) < len(best[1])):
                        best = (other, d)
            if best is not None:
                info["base"], info["delta"] = best
                info["depth"] = infos[best[0]]["depth"] + 1
            window.append(oid)
            if len(window) > DELTA_WINDOW:
                window.pop(0)
    # Write: every delta base before its delta.
    body = bytearray(b"PACK" + struct.pack(">II", 2, len(order)))
    offsets, crcs = {}, {}

    def emit(oid):
        if oid in offsets:
            return
        info = infos[oid]
        if info["base"] is not None:
            emit(info["base"])
        start = len(body)
        if info["base"] is not None:
            payload = info["delta"]
            head = _obj_header(OBJ_OFS_DELTA, len(payload)) + _ofs_encoding(start - offsets[info["base"]])
        else:
            payload = info["data"]
            head = _obj_header(info["type"], len(payload))
        chunk = head + zlib.compress(payload)
        body.extend(chunk)
        offsets[oid] = start
        crcs[oid] = zlib.crc32(chunk) & 0xFFFFFFFF

    for oid in order:
        emit(oid)
    checksum = hashlib.sha1(body).digest()
    body.extend(checksum)
    hexsum = checksum.hex()
    # `pack-objects <base-name>` writes <base-name>-<hash>.pack; gc writes
    # objects/pack/pack-<hash>.pack.
    stem = f"{base_name}-{hexsum}" if base_name else str(pack_dir / f"pack-{hexsum}")
    pack_path = Path(stem + ".pack")
    tmp = pack_path.with_name("tmp_" + pack_path.name)
    tmp.write_bytes(bytes(body))
    idx_bytes = build_index(offsets, crcs, checksum)
    tmp_idx = pack_path.with_name("tmp_" + pack_path.stem + ".idx")
    tmp_idx.write_bytes(idx_bytes)
    for p in (tmp, tmp_idx):
        os.chmod(p, 0o444)
    os.replace(tmp, pack_path)
    os.replace(tmp_idx, pack_path.with_suffix(".idx"))
    return pack_path, hexsum


def build_index(offsets: dict, crcs: dict, pack_checksum: bytes) -> bytes:
    ids = sorted(offsets)
    fan = [0] * 256
    for oid in ids:
        fan[int(oid[:2], 16)] += 1
    total = 0
    for i in range(256):
        total += fan[i]
        fan[i] = total
    out = bytearray(IDX_MAGIC + struct.pack(">I", 2) + struct.pack(">256I", *fan))
    for oid in ids:
        out += bytes.fromhex(oid)
    for oid in ids:
        out += struct.pack(">I", crcs[oid])
    large = []
    for oid in ids:
        off = offsets[oid]
        if off >= 0x80000000:
            out += struct.pack(">I", 0x80000000 | len(large))
            large.append(off)
        else:
            out += struct.pack(">I", off)
    for off in large:
        out += struct.pack(">Q", off)
    out += pack_checksum
    out += hashlib.sha1(out).digest()
    return bytes(out)


def index_existing_pack(pack_path: Path, out_idx: Path | None = None) -> tuple[Path, str]:
    """Build the .idx for a .pack (what `git index-pack` does)."""
    data = Path(pack_path).read_bytes()
    if data[:4] != b"PACK":
        raise GitError(f"{pack_path}: not a pack file")
    count = struct.unpack(">I", data[8:12])[0]
    if hashlib.sha1(data[:-20]).digest() != data[-20:]:
        raise GitError(f"{pack_path}: pack checksum mismatch")
    reader = Pack.__new__(Pack)
    reader._data = data
    reader._cache = OrderedDict()
    reader.oids, reader.offsets, reader._pos = [], [], {}
    entries = []  # (offset, end)
    pos = 12
    for _ in range(count):
        typ, size, base, dpos = reader._header(pos)
        _, end = reader._inflate(dpos, size)
        entries.append((pos, end, typ, base))
        pos = end
    offsets, crcs = {}, {}
    by_offset = {}
    pending = []
    for start, end, typ, base in entries:
        crc = zlib.crc32(data[start:end]) & 0xFFFFFFFF
        if typ in TYPE_NAMES:
            t, content = reader.read_at(start)
            oid = hashlib.sha1(TYPE_NAMES[t] + b" " + str(len(content)).encode() + b"\0" + content).hexdigest()
            offsets[oid], crcs[oid], by_offset[start] = start, crc, oid
        else:
            pending.append((start, crc, base))
    # REF_DELTA bases can be anywhere in the pack: resolve until stable.
    while pending:
        progress = False
        rest = []
        for start, crc, base in pending:
            if base[0] == "ref" and base[1] not in offsets:
                rest.append((start, crc, base))
                continue
            if base[0] == "ref":
                reader.oids = sorted(offsets)
                reader.offsets = [offsets[o] for o in reader.oids]
                reader._pos = {o: i for i, o in enumerate(reader.oids)}
            t, content = reader.read_at(start)
            oid = hashlib.sha1(TYPE_NAMES[t] + b" " + str(len(content)).encode() + b"\0" + content).hexdigest()
            offsets[oid], crcs[oid], by_offset[start] = start, crc, oid
            progress = True
        if not progress:
            raise GitError("pack has deltas with missing bases (thin pack)")
        pending = rest
    idx_path = Path(out_idx) if out_idx else Path(pack_path).with_suffix(".idx")
    tmp = idx_path.with_name("tmp_" + idx_path.name)
    tmp.write_bytes(build_index(offsets, crcs, data[-20:]))
    if idx_path.exists():
        os.chmod(idx_path, 0o644)  # pack indexes are read-only, like git's
    os.replace(tmp, idx_path)
    os.chmod(idx_path, 0o444)
    return idx_path, data[-20:].hex()

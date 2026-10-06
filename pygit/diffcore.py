"""Rename detection between deleted and added paths (git's diffcore-rename).

Exact renames (same blob) are paired first. Remaining pairs are scored by
content similarity the way git does it: files are cut into chunks (lines,
or 64-byte pieces for long lines), each chunk is hashed, and the score is
the number of bytes in common chunks divided by the larger file's size.
"""

from __future__ import annotations

from collections import Counter

MAX_SCORE = 60000


def _chunks(data: bytes) -> Counter:
    """Byte counts per chunk hash, like diffcore-delta's spanhash table."""
    out = Counter()
    n = len(data)
    i = 0
    while i < n:
        j = i
        h = 0
        # A chunk ends after a newline or 64 bytes.
        while j < n and j - i < 64:
            c = data[j]
            h = ((h << 7) ^ (h >> 25) ^ c) & 0xFFFFFFFF
            j += 1
            if c == 0x0A:
                break
        out[h] += j - i
        i = j
    return out


def similarity(a: bytes, b: bytes) -> int:
    """Similarity score in [0, MAX_SCORE]."""
    if not a and not b:
        return MAX_SCORE
    ca, cb = _chunks(a), _chunks(b)
    common = sum(min(v, cb[k]) for k, v in ca.items() if k in cb)
    base = max(len(a), len(b))
    return MAX_SCORE * common // base if base else 0


def detect_renames(odb, old: dict, new: dict, threshold_percent: int = 50):
    """Pair deleted paths (`old`: path -> (mode, oid)) with added ones.

    Returns [(src, dst, score_percent)], each source used at most once.
    """
    pairs = []
    old = dict(old)
    new = dict(new)
    # Exact matches first; prefer sources with the same basename.
    by_oid: dict[str, list[bytes]] = {}
    for p, (mode, oid) in sorted(old.items()):
        by_oid.setdefault(oid, []).append(p)
    for dst, (mode, oid) in sorted(new.items()):
        srcs = by_oid.get(oid)
        if not srcs:
            continue
        base = dst.rsplit(b"/", 1)[-1]
        pick = next((s for s in srcs if s.rsplit(b"/", 1)[-1] == base), srcs[0])
        srcs.remove(pick)
        pairs.append((pick, dst, 100))
        del old[pick]
        del new[dst]
    if not old or not new or odb is None:
        return pairs
    threshold = threshold_percent * MAX_SCORE // 100
    cache = {}

    def content(oid):
        if oid not in cache:
            cache[oid] = odb.read(oid)[1]
        return cache[oid]

    candidates = []
    for dst, (dmode, doid) in new.items():
        if dmode in (0o160000,):
            continue
        for src, (smode, soid) in old.items():
            if smode in (0o160000,) or (smode == 0o120000) != (dmode == 0o120000):
                continue
            a, b = content(soid), content(doid)
            # Size difference alone can rule out a pair.
            if max(len(a), len(b)) and min(len(a), len(b)) * MAX_SCORE < threshold * max(len(a), len(b)):
                continue
            s = similarity(a, b)
            if s >= threshold:
                candidates.append((s, dst, src))
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    used_src, used_dst = set(), set()
    for s, dst, src in candidates:
        if src in used_src or dst in used_dst:
            continue
        used_src.add(src)
        used_dst.add(dst)
        pairs.append((src, dst, s * 100 // MAX_SCORE))
    return pairs

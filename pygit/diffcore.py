"""Rename detection between deleted and added paths: a port of git's
diffcore-rename.c with diffcore-delta.c's similarity measure.

Order of git's passes (renames only, no copies):
  1. exact renames (same blob), preferring a source with the same basename;
  2. basename matches: a source and a destination whose basenames are each
     unique among the remaining files pair up if their similarity reaches
     the halfway point between the threshold and 100%;
  3. the full matrix: every remaining pair is scored, the best candidates
     sorted by score, and assigned greedily (each source used once).

Similarity (estimate_similarity): files are cut into spans ending at a
newline or after 64 bytes; each span's hash counts its length; the bytes
of the source found in the destination ("src_copied") divided by the
larger file's size gives the score, out of MAX_SCORE.
"""

from __future__ import annotations

from collections import Counter

MAX_SCORE = 60000
HASHBASE = 107927
NUM_CANDIDATE_PER_DST = 4
_MASK = 0xFFFFFFFF


def span_hashes(data: bytes, is_text: bool) -> Counter:
    """hash_chars(): span hash -> total bytes in spans with that hash."""
    counts = Counter()
    accum1 = accum2 = 0
    n = 0
    size = len(data)
    i = 0
    while i < size:
        c = data[i]
        i += 1
        # A CR right before LF is ignored in text files.
        if is_text and c == 0x0D and i < size and data[i] == 0x0A:
            continue
        old_1 = accum1
        accum1 = ((accum1 << 7) ^ (accum2 >> 25)) & _MASK
        accum2 = ((accum2 << 7) ^ (old_1 >> 25)) & _MASK
        accum1 = (accum1 + c) & _MASK
        n += 1
        if n < 64 and c != 0x0A:
            continue
        counts[(accum1 + accum2 * 0x61) % HASHBASE] += n
        n = 0
        accum1 = accum2 = 0
    if n > 0:
        counts[(accum1 + accum2 * 0x61) % HASHBASE] += n
    return counts


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def src_copied(src: Counter, dst: Counter) -> int:
    """diffcore_count_changes(): bytes of src that also appear in dst."""
    return sum(min(cnt, dst[h]) for h, cnt in src.items() if h in dst)


def _basename(p: bytes) -> bytes:
    return p.rsplit(b"/", 1)[-1]


class _Sizes:
    def __init__(self, odb):
        self.odb = odb
        self.data = {}
        self.spans = {}

    def get(self, oid):
        if oid not in self.data:
            self.data[oid] = self.odb.read(oid)[1]
        return self.data[oid]

    def span(self, oid):
        if oid not in self.spans:
            d = self.get(oid)
            self.spans[oid] = span_hashes(d, not _is_binary(d))
        return self.spans[oid]


def _is_reg(mode: int) -> bool:
    return (mode & 0o170000) == 0o100000


def estimate_similarity(cache: _Sizes, src, dst, minimum_score: int) -> int:
    smode, soid = src
    dmode, doid = dst
    if not _is_reg(smode) or not _is_reg(dmode):
        return 0
    ssize, dsize = len(cache.get(soid)), len(cache.get(doid))
    max_size, base_size = max(ssize, dsize), min(ssize, dsize)
    delta_size = max_size - base_size
    if max_size * (MAX_SCORE - minimum_score) < delta_size * MAX_SCORE:
        return 0
    if not dsize:
        return 0
    copied = src_copied(cache.span(soid), cache.span(doid))
    return copied * MAX_SCORE // max_size


def similarity(a: bytes, b: bytes) -> int:
    """Score of two blobs, in [0, MAX_SCORE] (for callers outside rename detection)."""
    if not a and not b:
        return MAX_SCORE
    sa, sb = span_hashes(a, not _is_binary(a)), span_hashes(b, not _is_binary(b))
    base = max(len(a), len(b))
    return src_copied(sa, sb) * MAX_SCORE // base if base else 0


def detect_renames(odb, old: dict, new: dict, threshold_percent: int = 50):
    """Pair deleted paths (`old`: path -> (mode, oid)) with added ones.

    Returns [(src, dst, score_percent)], each source used at most once.
    """
    pairs = []
    old = dict(old)
    new = dict(new)
    # 1. Exact renames.
    by_oid: dict[str, list[bytes]] = {}
    for p, (mode, oid) in sorted(old.items()):
        by_oid.setdefault(oid, []).append(p)
    for dst, (mode, oid) in sorted(new.items()):
        srcs = by_oid.get(oid)
        if not srcs:
            continue
        base = _basename(dst)
        pick = next((s for s in srcs if _basename(s) == base), srcs[0])
        srcs.remove(pick)
        pairs.append((pick, dst, 100))
        del old[pick]
        del new[dst]
    if not old or not new or odb is None:
        return pairs
    minimum = threshold_percent * MAX_SCORE // 100
    cache = _Sizes(odb)
    # 2. Unique basenames.
    min_basename = minimum + (MAX_SCORE - minimum) // 2
    src_names, dst_names = Counter(_basename(p) for p in old), Counter(_basename(p) for p in new)
    dst_by_name = {_basename(p): p for p in new if dst_names[_basename(p)] == 1}
    for src in sorted(old):
        name = _basename(src)
        if src_names[name] != 1 or name not in dst_by_name:
            continue
        dst = dst_by_name[name]
        if dst not in new:
            continue
        score = estimate_similarity(cache, old[src], new[dst], minimum)
        if score >= min_basename:
            pairs.append((src, dst, score * 100 // MAX_SCORE))
            del old[src]
            del new[dst]
    if not old or not new:
        return pairs
    # 3. The matrix: top candidates per destination, best first.
    srcs = sorted(old)
    candidates = []
    for di, dst in enumerate(sorted(new)):
        scored = []
        for si, src in enumerate(srcs):
            score = estimate_similarity(cache, old[src], new[dst], minimum)
            if score < minimum:
                continue
            scored.append((score, _basename(src) == _basename(dst), si, di, src, dst))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        candidates.extend(scored[:NUM_CANDIDATE_PER_DST])
    candidates.sort(key=lambda x: (-x[0], -x[1], x[3], x[2]))
    used_src, used_dst = set(), set()
    for score, _, _, _, src, dst in candidates:
        if src in used_src or dst in used_dst:
            continue
        used_src.add(src)
        used_dst.add(dst)
        pairs.append((src, dst, score * 100 // MAX_SCORE))
    return pairs

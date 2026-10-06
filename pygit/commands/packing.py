"""Stage 5 commands for packs: verify-pack, index-pack, pack-objects,
count-objects, pack-refs, prune, gc."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from pygit import pack as packmod
from pygit.cli import command, out
from pygit.errors import GitError
from pygit.history import CommitCache
from pygit.objects import Commit, Tag, parse_tree
from pygit.refs import Refs
from pygit.repo import find_repo


@command("verify-pack")
def cmd_verify_pack(args):
    verbose = stat_only = False
    paths = []
    for arg in args:
        if arg in ("-v", "--verbose"):
            verbose = True
        elif arg in ("-s", "--stat-only"):
            stat_only = True
        else:
            paths.append(arg)
    rc = 0
    for arg in paths:
        base = arg[:-4] if arg.endswith((".idx", ".pack")) else arg
        if arg.endswith(".pack"):
            base = arg[:-5]
        p = packmod.Pack(Path(base + ".idx"))
        import hashlib
        data = p.data
        if hashlib.sha1(data[:-20]).digest() != data[-20:]:
            out(f"{base}.pack: bad\n".encode())
            rc = 1
            continue
        if verbose or stat_only:
            out(_verify_listing(p, show_objects=not stat_only))
        if verbose and not stat_only:
            out(f"{base}.pack: ok\n".encode())
    return rc


def _verify_listing(p, show_objects: bool) -> bytes:
    by_offset_id = {p.offsets[i]: p.oids[i] for i in range(len(p.oids))}
    depth_of = {}
    lines = []
    histogram = {}
    non_delta = 0
    for i in p.by_offset:
        typ, size, packed, offset, base = p.entry_info(i)
        real_type, _ = p.read_at(offset)
        oid = p.oids[i]
        if base is None:
            depth_of[offset] = 0
            non_delta += 1
            line = f"{oid} {packmod.TYPE_NAMES[real_type].decode():<6} {size} {packed} {offset}"
        else:
            base_off = offset - base[1] if base[0] == "ofs" else p.offsets[p._pos[base[1]]]
            depth = depth_of.get(base_off, 0) + 1
            depth_of[offset] = depth
            histogram[depth] = histogram.get(depth, 0) + 1
            line = (f"{oid} {packmod.TYPE_NAMES[real_type].decode():<6} {size} {packed} {offset} "
                    f"{depth} {by_offset_id[base_off]}")
        lines.append(line + "\n")
    tail = [f"non delta: {non_delta} object{'s' if non_delta != 1 else ''}\n"]
    for depth in sorted(histogram):
        n = histogram[depth]
        tail.append(f"chain length = {depth}: {n} object{'s' if n != 1 else ''}\n")
    return ("".join(lines) if show_objects else "").encode() + "".join(tail).encode()


@command("index-pack")
def cmd_index_pack(args):
    out_idx = None
    files = []
    it = iter(args)
    for arg in it:
        if arg == "-o":
            out_idx = next(it)
        elif arg.startswith("-"):
            raise GitError(f"unsupported option '{arg}'")
        else:
            files.append(arg)
    if len(files) != 1:
        raise GitError("usage: git index-pack [-o <index-file>] <pack-file>")
    _, hexsum = packmod.index_existing_pack(Path(files[0]), Path(out_idx) if out_idx else None)
    out(hexsum.encode() + b"\n")
    return 0


def reachable_objects(repo, starts: list[str], cache=None, exclude: set | None = None,
                      exclude_commits: list[str] = ()):
    """[(oid, path name)] reachable from `starts`, in git's pack order:
    commits first (newest first), then tags, then trees and blobs in
    traversal order. `exclude` objects (and history behind
    `exclude_commits`) are left out."""
    from pygit.history import walk
    cache = cache or CommitCache(repo)
    exclude = exclude or set()
    commits, tags, others = [], [], []
    seen = set(exclude)
    commit_starts = []
    for oid in starts:
        cur = oid
        while True:
            t, data = repo.odb.read(cur)
            if t == b"tag":
                if cur not in seen:
                    seen.add(cur)
                    tags.append((cur, None))
                cur = Tag.parse(data).object
                continue
            if t == b"commit":
                commit_starts.append(cur)
            elif t == b"tree":
                others.append(("tree-root", cur))
            elif t == b"blob" and cur not in seen:
                seen.add(cur)
                others.append(("obj", (cur, None)))
            break
    for c in walk(repo, list(dict.fromkeys(commit_starts)), list(exclude_commits), cache=cache):
        if c in seen:
            continue
        seen.add(c)
        commits.append((c, None))
    tree_objs = []

    def visit_tree(tree_oid, prefix):
        if tree_oid in seen:
            return
        seen.add(tree_oid)
        tree_objs.append((tree_oid, prefix.rstrip(b"/") or None))
        for e in parse_tree(repo.odb.read(tree_oid)[1]):
            if e.mode == 0o160000:
                continue
            if e.is_tree:
                visit_tree(e.oid, prefix + e.name + b"/")
            elif e.oid not in seen:
                seen.add(e.oid)
                tree_objs.append((e.oid, prefix + e.name))

    for c, _ in commits:
        visit_tree(cache.get(c).tree, b"")
    for kind, v in others:
        if kind == "tree-root":
            visit_tree(v, b"")
        else:
            tree_objs.append(v)
    return commits + tags + tree_objs


@command("pack-objects")
def cmd_pack_objects(args):
    repo = find_repo()
    to_stdout = revs = False
    base = None
    for arg in args:
        if arg == "--stdout":
            to_stdout = True
        elif arg == "--revs":
            revs = True
        elif arg.startswith("-"):
            continue
        else:
            base = arg
    lines = sys.stdin.read().splitlines()
    if revs:
        from pygit import revparse
        inc, exc = [], []
        for l in lines:
            l = l.strip()
            if not l:
                continue
            if l.startswith("^"):
                exc.append(revparse.resolve(repo, l[1:]))
            else:
                inc.append(revparse.resolve(repo, l))
        hidden = {o for o, _ in reachable_objects(repo, exc)} if exc else set()
        exc_commits = [revparse.peel(repo, o, b"commit") for o in exc]
        objs = reachable_objects(repo, inc, exclude=hidden, exclude_commits=exc_commits)
    else:
        objs = []
        for l in lines:
            if not l.strip():
                continue
            oid, _, name = l.partition(" ")
            objs.append((oid, name.encode() if name else None))
    if to_stdout:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p, _ = packmod.write_pack(repo.odb, objs, Path(d))
            sys.stdout.buffer.write(p.read_bytes())
            os.chmod(p, 0o644)
            os.chmod(p.with_suffix(".idx"), 0o644)
        return 0
    if base is None:
        raise GitError("usage: git pack-objects [<options>] <base-name>")
    p, hexsum = packmod.write_pack(repo.odb, objs, Path(base).parent, base_name=base)
    out(hexsum.encode() + b"\n")
    return 0


@command("count-objects")
def cmd_count_objects(args):
    repo = find_repo()
    verbose = "-v" in args or "--verbose" in args
    count = size = 0
    objdir = repo.objects_dir
    for d in objdir.iterdir() if objdir.is_dir() else []:
        if len(d.name) == 2 and d.is_dir():
            for f in d.iterdir():
                if len(f.name) == 38:
                    count += 1
                    size += _disk_bytes(f)
    size //= 1024  # git sums bytes, then converts once
    if not verbose:
        out(f"{count} objects, {size} kilobytes\n".encode())
        return 0
    repo.odb.refresh_packs()
    packs = repo.odb.packs
    in_pack = sum(len(p.oids) for p in packs)
    size_pack = sum(_disk_bytes(p.pack_path) + _disk_bytes(p.idx_path) for p in packs) // 1024
    loose_ids = set()
    for d in objdir.iterdir():
        if len(d.name) == 2 and d.is_dir():
            loose_ids.update(d.name + f.name for f in d.iterdir() if len(f.name) == 38)
    prunable = sum(1 for o in loose_ids if any(p.contains(o) for p in packs))
    out((f"count: {count}\nsize: {size}\nin-pack: {in_pack}\npacks: {len(packs)}\n"
         f"size-pack: {size_pack}\nprune-packable: {prunable}\ngarbage: 0\nsize-garbage: 0\n").encode())
    return 0


def _disk_bytes(p: Path) -> int:
    """on_disk_bytes(): st_blocks * 512 where available, else the size."""
    st = p.stat()
    blocks = getattr(st, "st_blocks", None)
    return blocks * 512 if blocks is not None else st.st_size


def pack_refs(repo, all_refs: bool = True) -> None:
    """Move loose refs into packed-refs (tags with their peeled ids)."""
    refs = Refs(repo)
    current = refs.list("refs/")
    peeled = {}
    for name, oid in current.items():
        if name.startswith("refs/tags/"):
            t, data = repo.odb.read(oid)
            target = oid
            while t == b"tag":
                target = Tag.parse(data).object
                t, data = repo.odb.read(target)
            if target != oid:
                peeled[name] = target
    keep = {n: o for n, o in current.items() if all_refs or n.startswith("refs/tags/")}
    refs.write_packed({**refs.packed(), **keep}, peeled)
    for name in keep:
        p = repo.gitdir / name
        if p.is_file():
            p.unlink()
            d = p.parent
            while d.name not in ("heads", "tags", "remotes", "refs") and d != repo.gitdir:
                try:
                    d.rmdir()
                except OSError:
                    break
                d = d.parent


@command("pack-refs")
def cmd_pack_refs(args):
    repo = find_repo()
    pack_refs(repo, all_refs="--all" in args)
    return 0


def all_roots(repo) -> list[str]:
    """Everything gc must keep: refs, HEAD, reflog entries, the index, MERGE_HEAD/ORIG_HEAD."""
    refs = Refs(repo)
    roots = list(refs.list("refs/").values())
    head, _ = refs.resolve("HEAD")
    if head:
        roots.append(head)
    for name in ("ORIG_HEAD", "MERGE_HEAD", "FETCH_HEAD", "CHERRY_PICK_HEAD"):
        p = repo.gitdir / name
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                tok = line.split("\t")[0].strip()
                if len(tok) == 40 and repo.odb.exists(tok):
                    roots.append(tok)
    logs = repo.gitdir / "logs"
    if logs.is_dir():
        for dirpath, _, files in os.walk(logs):
            for f in files:
                for line in (Path(dirpath) / f).read_bytes().splitlines():
                    parts = line.split(b" ", 2)
                    for tok in parts[:2]:
                        o = tok.decode()
                        if o != "0" * 40 and repo.odb.exists(o):
                            roots.append(o)
    return list(dict.fromkeys(roots))


def index_objects(repo) -> list[tuple[str, bytes]]:
    from pygit.index import Index
    try:
        idx = Index.read(repo)
    except GitError:
        return []
    return [(e.oid, e.path) for e in idx.sorted_entries() if e.mode != 0o160000]


def repack(repo) -> None:
    """repack -a -d: one pack with every reachable object; old packs removed;
    unreachable packed objects are kept as loose objects (like -A)."""
    roots = all_roots(repo)
    objs = reachable_objects(repo, roots)
    seen = {o for o, _ in objs}
    for oid, path in index_objects(repo):
        if oid not in seen and repo.odb.exists(oid):
            objs.append((oid, path))
            seen.add(oid)
    old_packs = list(repo.odb.packs)
    # Packed but unreachable objects become loose so prune's grace period applies.
    for p in old_packs:
        for oid in p.oids:
            if oid not in seen and not repo.odb.loose_path(oid).exists():
                t, data = p.read(oid)
                repo.odb.write(t, data)
    if not objs:
        return
    new_path, _ = packmod.write_pack(repo.odb, objs, repo.objects_dir / "pack")
    for p in old_packs:
        if p.pack_path == new_path:
            continue
        for f in (p.pack_path, p.idx_path, p.pack_path.with_suffix(".rev"),
                  p.pack_path.with_suffix(".bitmap"), p.pack_path.with_suffix(".keep")):
            if f.suffix == ".keep":
                continue
            if f.exists():
                os.chmod(f, 0o644)
                f.unlink()
    repo.odb.refresh_packs()


def prune_packed(repo) -> int:
    repo.odb.refresh_packs()
    n = 0
    for d in list(repo.objects_dir.iterdir()):
        if len(d.name) == 2 and d.is_dir():
            for f in list(d.iterdir()):
                oid = d.name + f.name
                if len(f.name) == 38 and any(p.contains(oid) for p in repo.odb.packs):
                    os.chmod(f, 0o644)
                    f.unlink()
                    n += 1
            try:
                d.rmdir()
            except OSError:
                pass
    return n


def prune(repo, expire_seconds: float | None = 14 * 24 * 3600, dry_run: bool = False) -> list[str]:
    """Delete unreachable loose objects older than the grace period."""
    roots = all_roots(repo)
    keep = {o for o, _ in reachable_objects(repo, roots)} | {o for o, _ in index_objects(repo)}
    now = time.time()
    removed = []
    for d in list(repo.objects_dir.iterdir()):
        if len(d.name) == 2 and d.is_dir():
            for f in list(d.iterdir()):
                oid = d.name + f.name
                if len(f.name) != 38 or oid in keep:
                    continue
                if expire_seconds is not None and now - f.stat().st_mtime < expire_seconds:
                    continue
                removed.append(oid)
                if not dry_run:
                    os.chmod(f, 0o644)
                    f.unlink()
            if not dry_run:
                try:
                    d.rmdir()
                except OSError:
                    pass
    return sorted(removed)


@command("prune")
def cmd_prune(args):
    repo = find_repo()
    dry = "-n" in args or "--dry-run" in args
    expire = None
    for a in args:
        if a.startswith("--expire="):
            v = a.split("=", 1)[1]
            expire = None if v == "now" else _parse_expire(v)
    removed = prune(repo, expire, dry)
    if dry or "-v" in args:
        for oid in removed:
            t = repo.odb.read(oid)[0].decode() if dry else "object"
            out(f"{oid} {t}\n".encode())
    return 0


def _parse_expire(v: str) -> float:
    units = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
    parts = v.replace(".", " ").split()
    if len(parts) >= 2 and parts[0].isdigit():
        unit = parts[1].rstrip("s")
        if unit in units:
            return int(parts[0]) * units[unit]
    raise GitError(f"unsupported --expire value: {v}")


@command("gc")
def cmd_gc(args):
    repo = find_repo()
    expire = 14 * 24 * 3600
    for a in args:
        if a.startswith("--prune="):
            v = a.split("=", 1)[1]
            expire = None if v == "now" else _parse_expire(v)
    pack_refs(repo, all_refs=True)
    repack(repo)
    prune_packed(repo)
    prune(repo, expire)
    return 0

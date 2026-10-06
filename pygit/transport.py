"""Moving objects and refs between repositories on the local disk.

The "wire" is direct file access: the other repository is opened as a
`Repo`, its refs are read, and the objects the receiving side lacks are
written into the receiver as a new pack (`pack.write_pack` with the
sender's object store as the source).
"""

from __future__ import annotations

from pathlib import Path

from pygit import pack as packmod
from pygit.errors import GitError
from pygit.history import CommitCache, walk
from pygit.objects import Tag, parse_tree
from pygit.refs import Refs
from pygit.repo import Repo


def open_repo(path: str | Path) -> Repo:
    """Open a repository by path: a work tree (path/.git) or a git dir."""
    p = Path(path)
    if (p / ".git").is_dir():
        return Repo(p / ".git", p)
    if (p / "HEAD").is_file() and (p / "objects").is_dir():
        repo = Repo(p, None)
        return repo
    raise GitError(f"'{path}' does not appear to be a git repository\n"
                   "fatal: Could not read from remote repository.\n\n"
                   "Please make sure you have the correct access rights\n"
                   "and the repository exists.")


def url_of(path: str | Path) -> str:
    return str(Path(path).resolve()).replace("\\", "/")


def advertised_refs(repo: Repo) -> dict[str, str]:
    """What the remote offers: branches and tags (and other refs/*)."""
    return Refs(repo).list("refs/")


def remote_head_branch(repo: Repo) -> str | None:
    target = Refs(repo).head_branch()
    if target and target.startswith("refs/heads/"):
        return target[len("refs/heads/"):]
    return None


def peel_to_commit(repo: Repo, oid: str) -> str | None:
    t, data = repo.odb.read(oid)
    while t == b"tag":
        oid = Tag.parse(data).object
        t, data = repo.odb.read(oid)
    return oid if t == b"commit" else None


def objects_to_send(src: Repo, wants: list[str], dst: Repo) -> list[tuple[str, bytes | None]]:
    """Objects reachable from `wants` in src that dst does not have.

    dst's refs are the "haves": anything reachable from them is skipped,
    as are objects dst already stores.
    """
    cache = CommitCache(src)
    haves = []
    for oid in Refs(dst).list("refs/").values():
        if src.odb.exists(oid):
            c = peel_to_commit(src, oid)
            if c:
                haves.append(c)
    out: list[tuple[str, bytes | None]] = []
    seen = set()
    commits, tags, trees = [], [], []
    commit_wants = []
    for oid in wants:
        cur = oid
        while True:
            t, data = src.odb.read(cur)
            if t == b"tag":
                if cur not in seen and not dst.odb.exists(cur):
                    seen.add(cur)
                    tags.append((cur, None))
                cur = Tag.parse(data).object
                continue
            if t == b"commit":
                commit_wants.append(cur)
            break
    for c in walk(src, list(dict.fromkeys(commit_wants)), haves, cache=cache):
        if dst.odb.exists(c):
            continue
        seen.add(c)
        commits.append((c, None))

    def visit(tree_oid, prefix):
        if tree_oid in seen or dst.odb.exists(tree_oid):
            return
        seen.add(tree_oid)
        trees.append((tree_oid, prefix.rstrip(b"/") or None))
        for e in parse_tree(src.odb.read(tree_oid)[1]):
            if e.mode == 0o160000:
                continue
            if e.is_tree:
                visit(e.oid, prefix + e.name + b"/")
            elif e.oid not in seen and not dst.odb.exists(e.oid):
                seen.add(e.oid)
                trees.append((e.oid, prefix + e.name))

    for c, _ in commits:
        visit(cache.get(c).tree, b"")
    out = commits + tags + trees
    return out


def transfer(src: Repo, dst: Repo, wants: list[str]) -> int:
    """Copy what dst lacks; returns the number of objects sent."""
    objs = objects_to_send(src, wants, dst)
    if not objs:
        return 0
    packmod.write_pack(src.odb, objs, dst.objects_dir / "pack")
    dst.odb.refresh_packs()
    return len(objs)


def is_ancestor_in(repo: Repo, a: str, b: str) -> bool:
    from pygit.history import ancestors
    ca, cb = peel_to_commit(repo, a), peel_to_commit(repo, b)
    if ca is None or cb is None:
        return False
    return ca in ancestors(repo, [cb])


# -- refspecs ------------------------------------------------------------------------

def parse_refspec(spec: str) -> tuple[bool, str, str]:
    force = spec.startswith("+")
    if force:
        spec = spec[1:]
    src, _, dst = spec.partition(":")
    return force, src, dst


def map_ref(refspec: str, ref: str) -> str | None:
    """Apply a fetch refspec (`+refs/heads/*:refs/remotes/origin/*`) to a ref."""
    _, src, dst = parse_refspec(refspec)
    if "*" in src:
        pre, _, post = src.partition("*")
        if ref.startswith(pre) and ref.endswith(post) and len(ref) >= len(pre) + len(post):
            mid = ref[len(pre):len(ref) - len(post)] if post else ref[len(pre):]
            return dst.replace("*", mid)
        return None
    return dst if ref == src else None


def shorten(ref: str) -> str:
    for pre in ("refs/heads/", "refs/tags/", "refs/remotes/", "refs/"):
        if ref.startswith(pre):
            return ref[len(pre):]
    return ref

"""Revision parsing: turning `HEAD~2^{tree}:src/x.py` into an object id."""

from __future__ import annotations

import re

from pygit.errors import GitError
from pygit.objects import Commit, Tag, parse_tree

_HEX = re.compile(r"^[0-9a-fA-F]{4,40}$")


def dwim_ref_names(name: str) -> list[str]:
    """The refs git tries for a short name, in its order (refs.c ref_rev_parse_rules)."""
    return [name, f"refs/{name}", f"refs/tags/{name}", f"refs/heads/{name}",
            f"refs/remotes/{name}", f"refs/remotes/{name}/HEAD"]


def resolve_ref_name(repo, name: str) -> tuple[str, str] | None:
    """(oid, full refname) for a short or full ref name, or None."""
    from pygit.refs import Refs
    refs = Refs(repo)
    for cand in dwim_ref_names(name):
        # The bare name is only a ref at the top level for HEAD-like names.
        if cand == name and not (name.startswith("refs/") or
                                 name in ("HEAD", "FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD")):
            continue
        oid, _ = refs.resolve(cand)
        if oid:
            return oid, cand
    return None


def peel(repo, oid: str, want: bytes | None) -> str:
    """Peel tags (and commits, for `tree`) until an object of type `want`.

    `want=None` peels tags only (the `^{}` suffix).
    """
    while True:
        t, data = repo.odb.read(oid)
        if want is not None and t == want:
            return oid
        if t == b"tag":
            oid = Tag.parse(data).object
            continue
        if want is None:
            return oid
        if t == b"commit" and want == b"tree":
            return Commit.parse(data).tree
        raise GitError(f"object {oid} is a {t.decode()}, not a {want.decode()}")


def reflog_entries(repo, refname: str) -> list[tuple[str, str, bytes]]:
    """(old, new, message) for each reflog line, oldest first."""
    p = repo.gitdir / "logs" / refname
    try:
        lines = p.read_bytes().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        meta, _, msg = line.partition(b"\t")
        parts = meta.split(b" ")
        if len(parts) >= 2:
            out.append((parts[0].decode(), parts[1].decode(), msg))
    return out


def _reflog_nth(repo, name: str, n: int) -> str:
    """`<ref>@{n}`: the value the ref had n updates ago."""
    got = resolve_ref_name(repo, name or "HEAD")
    entries = reflog_entries(repo, got[1] if got else name)
    if n >= len(entries):
        raise GitError(f"log for '{name or 'HEAD'}' only has {len(entries)} entries")
    return entries[len(entries) - 1 - n][1]


def _base(repo, name: str) -> str:
    m = re.match(r"^(.*)@\{(\d+)\}$", name)
    if m:
        return _reflog_nth(repo, m.group(1), int(m.group(2)))
    if name in ("", "@"):
        name = "HEAD"
    got = resolve_ref_name(repo, name)
    if got:
        return got[0]
    if _HEX.match(name):
        prefix = name.lower()
        if len(prefix) == 40:
            return prefix
        matches = repo.odb.find_prefix(prefix)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise GitError(f"short object ID {prefix} is ambiguous")
    raise GitError(f"ambiguous argument '{name}': unknown revision or path not in the working tree.\n"
                   "Use '--' to separate paths from revisions, like this:\n"
                   "'git <command> [<revision>...] -- [<file>...]'")


class PathNotFound(GitError):
    """`<rev>:<path>` named a valid revision but a missing path."""


def lookup_path(repo, tree_oid: str, path: str, rev_name: str | None = None) -> str:
    """The object id at `path` inside a tree."""
    oid = tree_oid
    for part in [p for p in path.split("/") if p]:
        t, data = repo.odb.read(oid)
        found = None
        if t == b"tree":
            found = next((e.oid for e in parse_tree(data) if e.name == part.encode()), None)
        if found is None:
            raise PathNotFound(f"path '{path}' does not exist in '{rev_name or tree_oid}'")
        oid = found
    return oid


_SUFFIX = re.compile(r"(\^\{[^}]*\}|\^\d*|~\d*)")


def resolve(repo, spec: str) -> str:
    """Resolve a revision expression to an object id."""
    # `<rev>:<path>` and `:<path>` / `:<n>:<path>` (index)
    if ":" in spec:
        rev, _, path = spec.partition(":")
        if rev == "":
            from pygit.index import Index
            stage = 0
            m = re.match(r"^([0-3]):(.*)$", path)
            if m:
                stage, path = int(m.group(1)), m.group(2)
            idx = Index.read(repo)
            e = idx.get(path.encode(), stage)
            if e is None:
                raise PathNotFound(f"path '{path}' does not exist (neither on disk nor in the index)")
            return e.oid
        tree = peel(repo, resolve(repo, rev), b"tree")
        return lookup_path(repo, tree, path, rev) if path else tree

    m = re.match(r"^(.*?)((?:\^\{[^}]*\}|\^\d*|~\d*)*)$", spec)
    base, suffixes = m.group(1), m.group(2)
    oid = _base(repo, base)
    for s in _SUFFIX.findall(suffixes):
        if s.startswith("^{"):
            kind = s[2:-1]
            if kind == "":
                oid = peel(repo, oid, None)
            elif kind in ("commit", "tree", "blob", "tag"):
                if kind == "tag":
                    t, _ = repo.odb.read(oid)
                    if t != b"tag":
                        raise GitError(f"{spec}: expected tag type, but the object dereferences to {t.decode()} type")
                else:
                    oid = peel(repo, oid, kind.encode())
            elif kind == "object":
                repo.odb.read(oid)
            else:
                raise GitError(f"ambiguous argument '{spec}'")
        elif s.startswith("^"):
            n = int(s[1:]) if len(s) > 1 else 1
            c = Commit.parse(repo.odb.read(peel(repo, oid, b"commit"))[1])
            if n == 0:
                oid = peel(repo, oid, b"commit")
            elif n > len(c.parents):
                raise GitError(f"ambiguous argument '{spec}': unknown revision or path not in the working tree.")
            else:
                oid = c.parents[n - 1]
        else:  # ~N
            n = int(s[1:]) if len(s) > 1 else 1
            oid = peel(repo, oid, b"commit")
            for _ in range(n):
                c = Commit.parse(repo.odb.read(oid)[1])
                if not c.parents:
                    raise GitError(f"ambiguous argument '{spec}': unknown revision or path not in the working tree.")
                oid = c.parents[0]
    return oid


def short_id(repo, oid: str, minimum: int = 7) -> str:
    """Shortest unambiguous prefix of at least `minimum` characters."""
    n = minimum
    while n < 40:
        if len(repo.odb.find_prefix(oid[:n])) <= 1:
            return oid[:n]
        n += 1
    return oid

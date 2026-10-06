"""Patch ids: a hash of a commit's diff that ignores whitespace and line
numbers, so the same change made twice gets the same id. A port of
diff.c's diff_get_patch_id (the "unstable" form rebase uses: no rename
detection, three lines of context, no hunk headers)."""

from __future__ import annotations

import hashlib

from pygit import xdiff
from pygit.objects import Commit
from pygit.treediff import diff_maps, tree_entries

_SPACE = b" \t\n\r\x0b\x0c"


def _strip(b: bytes) -> bytes:
    return bytes(c for c in b if c not in _SPACE)


def patch_id(repo, oid: str) -> str | None:
    """None for merges (git does not compute them)."""
    c = Commit.parse(repo.odb.read(oid)[1])
    if len(c.parents) > 1:
        return None
    old = tree_entries(repo.odb, Commit.parse(repo.odb.read(c.parents[0])[1]).tree) if c.parents else {}
    new = tree_entries(repo.odb, c.tree)
    h = hashlib.sha1()
    for p in diff_maps(repo.odb, old, new, renames=False):
        one_path = p.old_path if p.old_path is not None else p.new_path
        two_path = p.new_path if p.new_path is not None else p.old_path
        a, b = _strip(one_path), _strip(two_path)
        h.update(b"diff--git" + b"a/" + a + b"b/" + b)
        if p.status == "A":
            h.update(b"newfilemode" + b"%06o" % p.new_mode)
        elif p.status == "D":
            h.update(b"deletedfilemode" + b"%06o" % p.old_mode)
        elif p.old_mode != p.new_mode:
            h.update(b"oldmode" + b"%06o" % p.old_mode + b"newmode" + b"%06o" % p.new_mode)
        da = repo.odb.read(p.old_oid)[1] if p.old_oid else b""
        db = repo.odb.read(p.new_oid)[1] if p.new_oid else b""
        if xdiff.is_binary(da) or xdiff.is_binary(db):
            h.update((p.old_oid or "0" * 40).encode() + (p.new_oid or "0" * 40).encode())
            continue
        if p.status == "A":
            h.update(b"---/dev/null" + b"+++b/" + b)
        elif p.status == "D":
            h.update(b"---a/" + a + b"+++/dev/null")
        else:
            h.update(b"---a/" + a + b"+++b/" + b)
        la, lb = xdiff.split_lines(da), xdiff.split_lines(db)
        body = xdiff.unified(la, lb, xdiff.diff(la, lb), 3, funcnames=False)
        for line in body.split(b"\n"):
            if not line or line.startswith(b"@@"):
                continue  # XDL_EMIT_NO_HUNK_HDR
            if len(line) + 1 > 12 and line.startswith(b"\\ "):
                continue  # "\ No newline at end of file"
            h.update(_strip(line))
    return h.hexdigest()

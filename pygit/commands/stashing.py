"""stash: saving and restoring work in progress, with stash commits shaped
as git makes them so either tool can read the other's stashes.

A stash entry is a commit W whose tree is the work tree's tracked files,
with parents HEAD and I, where I is a commit of the index (parent HEAD).
`refs/stash` points at the newest W; its reflog is the stack.
"""

from __future__ import annotations

import os
import re

from pygit import revparse, worktree
from pygit.cli import command, err, out
from pygit.errors import GitError
from pygit.index import Index, IndexEntry
from pygit.objects import Commit
from pygit.pretty import split_message
from pygit.refs import Refs
from pygit.repo import find_repo

STASH = "refs/stash"


def _entries(repo):
    """Stash entries newest first: (oid, message)."""
    return [(new, msg) for _, new, msg in reversed(revparse.reflog_entries(repo, STASH))]


def _head_label(repo) -> tuple[str, str, Commit]:
    refs = Refs(repo)
    head, _ = refs.resolve("HEAD")
    if head is None:
        raise GitError("You do not have the initial commit yet")
    target = refs.head_branch()
    branch = target[len("refs/heads/"):] if target and target.startswith("refs/heads/") else "(no branch)"
    c = Commit.parse(repo.odb.read(head)[1])
    return head, branch, c


def _push(repo, message: str | None, quiet: bool) -> int:
    from pygit.ident import ident
    from pygit.status import compute
    head, branch, hc = _head_label(repo)
    st = compute(repo, untracked_mode="no", detect_renames=False)
    if not st.staged and not st.unstaged and not st.unmerged:
        out(b"No local changes to save\n")
        return 0
    if st.unmerged:
        err("error: could not save the current state: unmerged paths\n")
        return 1
    idx = Index.read(repo)
    i_tree = idx.write_tree(repo.odb)
    subject = split_message(hc.message)[0].decode("utf-8", "replace")
    desc = f"{branch}: {revparse.short_id(repo, head)} {subject}"
    who = ident(repo, "committer")
    author = ident(repo, "author")
    i_commit = Commit(i_tree, [head], author, who, f"index on {desc}\n".encode())
    i_oid = repo.odb.write(b"commit", i_commit.serialize())
    # The work tree: the index with tracked files replaced by their current content.
    w_idx = Index(repo.gitdir / "index.stash")
    for e in idx.sorted_entries():
        st_ = worktree.lstat(repo, e.path)
        if st_ is None:
            continue
        if worktree.is_modified(repo, e, st_):
            w_idx.entries[(e.path, 0)] = worktree.entry_for_file(repo, e.path, e, write=True)
        else:
            w_idx.entries[(e.path, 0)] = e
    w_tree = w_idx.write_tree(repo.odb)
    w_msg = (f"On {branch}: {message}" if message else f"WIP on {desc}")
    # git writes this message without a final newline (the index commit has one).
    w_commit = Commit(w_tree, [head, i_oid], author, who, w_msg.encode())
    w_oid = repo.odb.write(b"commit", w_commit.serialize())
    refs = Refs(repo)
    old, _ = refs.resolve(STASH)
    log = repo.gitdir / "logs" / STASH
    log.parent.mkdir(parents=True, exist_ok=True)
    log.touch(exist_ok=True)  # refs/stash always keeps a reflog
    refs.update(STASH, w_oid, w_msg)
    # Back to HEAD, like reset --hard for tracked files.
    _reset_to_head(repo, hc.tree, idx)
    if not quiet:
        out(f"Saved working directory and index state {w_msg}\n".encode())
    return 0


def _reset_to_head(repo, head_tree: str, old_idx: Index) -> None:
    """Index and tracked files back to HEAD; files only in the index go."""
    from pygit.checkout import remove_file, tree_map, write_file
    head = tree_map(repo.odb, head_tree)
    new_idx = Index(old_idx.path)
    for p, (m, o) in head.items():
        e = old_idx.get(p)
        st = worktree.lstat(repo, p)
        if e is not None and (e.mode, e.oid) == (m, o) and st is not None and not worktree.is_modified(repo, e, st):
            new_idx.entries[(p, 0)] = e
            continue
        st = write_file(repo, p, m, o)
        ne = IndexEntry(path=p, oid=o, mode=m)
        ne.set_stat(st)
        new_idx.entries[(p, 0)] = ne
    for p in old_idx.paths() - set(head):
        remove_file(repo, p)
    new_idx.write()


def _resolve_stash(repo, spec: str | None) -> tuple[str, str]:
    """(oid, label for messages) of a stash entry."""
    entries = _entries(repo)
    if not entries:
        raise GitError("No stash entries found.")
    if spec is None:
        return entries[0][0], f"{STASH}@{{0}}"
    m = re.match(r"^(?:stash)?@\{(\d+)\}$", spec) or re.match(r"^(\d+)$", spec)
    if m:
        n = int(m.group(1))
        if n >= len(entries):
            raise GitError(f"log for 'refs/stash' only has {len(entries)} entries")
        return entries[n][0], spec if spec.startswith("stash") else f"stash@{{{n}}}"
    return revparse.resolve(repo, spec), spec


def _apply(repo, oid: str, restore_index: bool) -> int:
    from pygit import merge_ort
    from pygit.commands.merging import _config_style, apply_result
    from pygit.commands.porcelain import long_status
    from pygit.pathspec import cwd_prefix
    from pygit.status import compute, head_tree_entries
    from pygit.treediff import tree_entries
    w = Commit.parse(repo.odb.read(oid)[1])
    b_tree = Commit.parse(repo.odb.read(w.parents[0])[1]).tree
    i_tree = Commit.parse(repo.odb.read(w.parents[1])[1]).tree if len(w.parents) > 1 else b_tree
    head, _, hc = _head_label(repo)
    idx = Index.read(repo)
    head_map = head_tree_entries(repo)
    index_map = {e.path: (e.mode, e.oid) for e in idx.sorted_entries()}
    if restore_index and i_tree != b_tree and index_map != head_map:
        raise GitError("Cannot apply stash: Your index contains uncommitted changes.")
    merger = merge_ort.Merger(repo, "Updated upstream", "Stashed changes", "Stash base", style=_config_style())
    res = merger.merge_trees(b_tree, hc.tree, w.tree)
    applied = apply_result(repo, idx, head_map, res)
    if isinstance(applied, tuple):
        from pygit.checkout import conflict_message
        local, untracked = applied
        err(conflict_message(local, untracked, "merge", "merge"))
        return 1
    for line in res.sorted_messages():
        out(line.encode() + b"\n")
    if not res.clean:
        out(long_status(repo, compute(repo), cwd_prefix(repo)))
        return 1
    new_idx = Index.read(repo)
    if restore_index:
        # The index as stashed: the stash's index changes on top of HEAD.
        staged = tree_entries(repo.odb, i_tree)
        base = tree_entries(repo.odb, b_tree)
        wanted = dict(head_map)
        for p in set(base) | set(staged):
            if base.get(p) != staged.get(p):
                if p in staged:
                    wanted[p] = staged[p]
                else:
                    wanted.pop(p, None)
    else:
        # Everything unstaged, except files the stash added (they stay added).
        wanted = dict(head_map)
        for e in new_idx.sorted_entries():
            if e.path not in head_map:
                wanted[e.path] = (e.mode, e.oid)
    for e in list(new_idx.sorted_entries()):
        if e.path not in wanted:
            new_idx.remove(e.path)
    for p, (m, o) in wanted.items():
        cur = new_idx.get(p)
        if cur is None or (cur.mode, cur.oid) != (m, o):
            new_idx.add(IndexEntry(path=p, oid=o, mode=m))
    new_idx.write()
    out(long_status(repo, compute(repo), cwd_prefix(repo)))
    return 0


def _drop(repo, oid: str, label: str, quiet: bool) -> None:
    entries = _entries(repo)
    keep = [e for e in revparse.reflog_entries(repo, STASH)]
    pos = next(i for i, (o, _) in enumerate(entries) if o == oid)
    idx_in_log = len(keep) - 1 - pos
    del keep[idx_in_log]
    log = repo.gitdir / "logs" / STASH
    refs = Refs(repo)
    if not keep:
        refs.delete(STASH)
        if log.exists():
            log.unlink()
    else:
        # Rewrite the log so each entry's "old" is the previous entry's "new".
        raw = log.read_bytes().splitlines()
        del raw[idx_in_log]
        fixed = []
        prev = "0" * 40
        for line in raw:
            meta, _, msg = line.partition(b"\t")
            parts = meta.split(b" ")
            parts[0] = prev.encode()
            prev = parts[1].decode()
            fixed.append(b" ".join(parts) + b"\t" + msg)
        log.write_bytes(b"\n".join(fixed) + b"\n")
        from pygit.lockfile import write_locked
        write_locked(repo.gitdir / STASH, (keep[-1][1] + "\n").encode())
    if not quiet:
        out(f"Dropped {label} ({oid})\n".encode())


@command("stash")
def cmd_stash(args):
    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    sub = args[0] if args and not args[0].startswith("-") else "push"
    rest = args[1:] if args and not args[0].startswith("-") else list(args)
    quiet = "-q" in rest or "--quiet" in rest
    rest = [a for a in rest if a not in ("-q", "--quiet")]
    if sub in ("push", "save"):
        message = None
        it = iter(rest)
        for a in it:
            if a in ("-m", "--message"):
                message = next(it)
            elif a.startswith("-"):
                raise GitError(f"unsupported stash option '{a}'")
            elif sub == "save":
                message = " ".join([a] + list(it))
        return _push(repo, message, quiet)
    if sub == "list":
        for n, (oid, msg) in enumerate(_entries(repo)):
            out(f"stash@{{{n}}}: ".encode() + msg + b"\n")
        return 0
    if sub == "show":
        patch = "-p" in rest or "--patch" in rest
        specs = [a for a in rest if not a.startswith("-")]
        try:
            oid, _ = _resolve_stash(repo, specs[0] if specs else None)
        except GitError as e:
            err(e.message + "\n")
            return 1
        from pygit.commands.diffing import cmd_diff
        return cmd_diff(([] if patch else ["--stat"]) + [oid + "^1", oid])
    if sub in ("apply", "pop"):
        restore_index = "--index" in rest
        specs = [a for a in rest if not a.startswith("-")]
        try:
            oid, label = _resolve_stash(repo, specs[0] if specs else None)
        except GitError as e:
            err(e.message + "\n")
            return 1
        rc = _apply(repo, oid, restore_index)
        if rc:
            if sub == "pop":
                out(b"The stash entry is kept in case you need it again.\n")
            return rc
        if sub == "pop":
            _drop(repo, oid, label, quiet)
        return 0
    if sub == "drop":
        specs = [a for a in rest if not a.startswith("-")]
        try:
            oid, label = _resolve_stash(repo, specs[0] if specs else None)
        except GitError as e:
            err(e.message + "\n")
            return 1
        _drop(repo, oid, label, quiet)
        return 0
    if sub == "clear":
        refs = Refs(repo)
        if refs.exists(STASH):
            refs.delete(STASH)
        log = repo.gitdir / "logs" / STASH
        if log.exists():
            log.unlink()
        return 0
    raise GitError(f"unknown stash subcommand '{sub}'")

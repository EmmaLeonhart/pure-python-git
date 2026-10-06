"""cherry-pick: replaying commits with the merge machinery, and the
sequencer state git keeps for a multi-commit pick (`.git/sequencer`)."""

from __future__ import annotations

import os
import shutil

from pygit import revparse
from pygit.cli import command, err, out
from pygit.errors import GitError
from pygit.index import Index
from pygit.objects import Commit
from pygit.pretty import cleanup_message, split_message
from pygit.refs import Refs
from pygit.repo import find_repo

CONFLICT_HINT = (
    "hint: After resolving the conflicts, mark them with\n"
    "hint: \"git add/rm <pathspec>\", then run\n"
    "hint: \"git cherry-pick --continue\".\n"
    "hint: You can instead skip this commit with \"git cherry-pick --skip\".\n"
    "hint: To abort and get back to the state before \"git cherry-pick\",\n"
    "hint: run \"git cherry-pick --abort\".\n"
    "hint: Disable this message with \"git config set advice.mergeConflict false\"\n")

EMPTY_MESSAGE = (
    "The previous cherry-pick is now empty, possibly due to conflict resolution.\n"
    "If you wish to commit it anyway, use:\n\n"
    "    git commit --allow-empty\n\n"
    "Otherwise, please use 'git cherry-pick --skip'\n")


class Fatal(Exception):
    """A pick that fails before touching anything (exit 128)."""


def _write(repo, name: str, data: bytes) -> None:
    from pygit.lockfile import write_locked
    p = repo.gitdir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    write_locked(p, data)


def _remove(repo, *names) -> None:
    for n in names:
        p = repo.gitdir / n
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def _subject(c: Commit) -> str:
    return split_message(c.message)[0].decode("utf-8", "replace")


def apply_commit(repo, oid: str, action: str = "cherry-pick"):
    """Merge one commit's changes onto HEAD (base: its parent) into the
    index and work tree, printing the merge messages. Returns (merge
    result, new index, commit, short id, subject, HEAD tree); raises
    Fatal when nothing was touched."""
    from pygit import merge_ort
    from pygit.checkout import conflict_message
    from pygit.commands.merging import _config_style, apply_result
    from pygit.status import head_tree_entries
    refs = Refs(repo)
    head, _ = refs.resolve("HEAD")
    c = Commit.parse(repo.odb.read(oid)[1])
    if len(c.parents) > 1:
        raise Fatal(f"error: commit {oid} is a merge but no -m option was given.\n")
    idx = Index.read(repo)
    head_map = head_tree_entries(repo)
    index_map = {e.path: (e.mode, e.oid) for e in idx.sorted_entries()}
    if head_map != index_map:
        raise Fatal(f"error: your local changes would be overwritten by {action}.\n"
                    "hint: commit your changes or stash them to proceed.\n")
    short = revparse.short_id(repo, oid)
    subject = _subject(c)
    base_tree = Commit.parse(repo.odb.read(c.parents[0])[1]).tree if c.parents else None
    head_tree = Commit.parse(repo.odb.read(head)[1]).tree if head else None
    merger = merge_ort.Merger(repo, "HEAD", f"{short} ({subject})", f"parent of {short} ({subject})",
                              style=_config_style())
    res = merger.merge_trees(base_tree, head_tree, c.tree)
    applied = apply_result(repo, idx, head_map, res)
    if isinstance(applied, tuple):
        local, untracked = applied
        raise Fatal(conflict_message(local, untracked, "merge", "merge"))
    for line in res.sorted_messages():
        out(line.encode() + b"\n")
    return res, applied, c, short, subject, head_tree


def pick(repo, oid: str, record_origin: bool = False, no_commit: bool = False) -> int:
    """Apply one commit on top of HEAD. Returns 0 (picked), 1 (stopped:
    conflict or empty), raising Fatal when nothing was done."""
    head, _ = Refs(repo).resolve("HEAD")
    res, applied, c, short, subject, head_tree = apply_commit(repo, oid)
    message = c.message
    if record_origin:
        message = message.rstrip(b"\n") + b"\n\n(cherry picked from commit " + oid.encode() + b")\n"
    if not res.clean:
        conflicts = sorted({p for p, s in res.entries if s})
        if not no_commit:
            _write(repo, "CHERRY_PICK_HEAD", (oid + "\n").encode())
        _write(repo, "MERGE_MSG", message.rstrip(b"\n") + b"\n\n# Conflicts:\n" +
               b"".join(b"#\t" + p + b"\n" for p in conflicts))
        if no_commit:
            err("error: could not apply " + f"{short}... {subject}\n" +
                "hint: after resolving the conflicts, mark the corrected paths\n"
                "hint: with 'git add <paths>' or 'git rm <paths>'\n"
                "hint: Disable this message with \"git config set advice.mergeConflict false\"\n")
        else:
            err(f"error: could not apply {short}... {subject}\n" + CONFLICT_HINT)
        return 1
    if no_commit:
        _write(repo, "MERGE_MSG", message)
        return 0
    tree = applied.write_tree(repo.odb)
    if tree == head_tree:
        _write(repo, "CHERRY_PICK_HEAD", (oid + "\n").encode())
        _write(repo, "MERGE_MSG", message)
        err(EMPTY_MESSAGE)
        from pygit.commands.porcelain import long_status
        from pygit.pathspec import cwd_prefix
        from pygit.status import compute
        out(long_status(repo, compute(repo), cwd_prefix(repo)))
        return 1
    _commit(repo, head, tree, c, message)
    return 0


def _commit(repo, head: str, tree: str, original: Commit, message: bytes) -> str:
    """Record a picked commit: original author, new committer."""
    from pygit.commands.committing import commit_summary
    from pygit.ident import ident
    refs = Refs(repo)
    new = Commit(tree, [head] if head else [], original.author, ident(repo, "committer"),
                 cleanup_message(message, "whitespace"))
    oid = repo.odb.write(b"commit", new.serialize())
    refs.update("HEAD", oid, f"cherry-pick: {_subject(new)}")
    target = refs.head_branch()
    label = target[len("refs/heads/"):].encode() if target and target.startswith("refs/heads/") \
        else b"detached HEAD"
    out(commit_summary(repo, oid, new, label, root=not head, date_interesting=True))
    return oid


def _todo(repo) -> list[str]:
    p = repo.gitdir / "sequencer" / "todo"
    if not p.is_file():
        return []
    # Entries name abbreviated ids, as git writes them.
    return [revparse.resolve(repo, line.split()[1])
            for line in p.read_text(encoding="utf-8").splitlines() if line.startswith("pick ")]


def _save_sequencer(repo, orig_head: str, remaining: list[str]) -> None:
    lines = []
    for oid in remaining:
        c = Commit.parse(repo.odb.read(oid)[1])
        lines.append(f"pick {revparse.short_id(repo, oid)} {_subject(c)}\n")
    head, _ = Refs(repo).resolve("HEAD")
    _write(repo, "sequencer/head", (orig_head + "\n").encode())
    _write(repo, "sequencer/todo", "".join(lines).encode())
    _write(repo, "sequencer/abort-safety", ((head or "") + "\n").encode())


def run_picks(repo, oids: list[str], record_origin: bool, no_commit: bool, orig_head: str | None) -> int:
    for i, oid in enumerate(oids):
        try:
            rc = pick(repo, oid, record_origin, no_commit)
        except Fatal as e:
            err(str(e) + "fatal: cherry-pick failed\n")
            return 128
        if rc:
            if len(oids) > 1 or (repo.gitdir / "sequencer").is_dir():
                _save_sequencer(repo, orig_head, oids[i + 1:])
            return rc
    _remove(repo, "sequencer")
    return 0


def _reset_touched(repo) -> None:
    """Back to HEAD for every path the stopped pick touched."""
    from pygit.commands.merging import _abort
    _abort(repo)


@command("cherry-pick")
def cmd_cherry_pick(args):
    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    record_origin = no_commit = False
    action = None
    revs = []
    for arg in args:
        if arg == "-x":
            record_origin = True
        elif arg in ("-n", "--no-commit"):
            no_commit = True
        elif arg in ("--continue", "--abort", "--skip", "--quit"):
            action = arg[2:]
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            revs.append(arg)
    refs = Refs(repo)
    in_progress = (repo.gitdir / "CHERRY_PICK_HEAD").is_file() or (repo.gitdir / "sequencer").is_dir()
    if action:
        if not in_progress:
            err("error: no cherry-pick or revert in progress\nfatal: cherry-pick failed\n")
            return 128
        orig = (repo.gitdir / "sequencer" / "head").read_text().strip() \
            if (repo.gitdir / "sequencer" / "head").is_file() else None
        todo = _todo(repo)
        if action == "quit":
            _remove(repo, "sequencer", "CHERRY_PICK_HEAD")
            return 0
        if action == "abort":
            _reset_touched(repo)
            _remove(repo, "CHERRY_PICK_HEAD", "MERGE_MSG")
            if orig:
                from pygit.checkout import switch_trees
                head, _ = refs.resolve("HEAD")
                switch_trees(repo, Commit.parse(repo.odb.read(head)[1]).tree,
                             Commit.parse(repo.odb.read(orig)[1]).tree, force=True)
                refs.update("HEAD", orig, "cherry-pick: abort")
            _remove(repo, "sequencer")
            return 0
        if action == "skip":
            _reset_touched(repo)
            _remove(repo, "CHERRY_PICK_HEAD", "MERGE_MSG")
        else:  # continue
            idx = Index.read(repo)
            if idx.has_conflicts():
                err("error: Committing is not possible because you have unmerged files.\n"
                    "hint: Fix them up in the work tree, and then use 'git add/rm <file>'\n"
                    "hint: as appropriate to mark resolution and make a commit.\n"
                    "fatal: cherry-pick failed\n")
                return 128
            if (repo.gitdir / "CHERRY_PICK_HEAD").is_file():
                rc = continue_commit(repo)
                if rc:
                    return rc
        return run_picks(repo, todo, record_origin, no_commit, orig)
    if Index.read(repo).has_conflicts():
        err("error: Cherry-picking is not possible because you have unmerged files.\n"
            "hint: Fix them up in the work tree, and then use 'git add/rm <file>'\n"
            "hint: as appropriate to mark resolution and make a commit.\n"
            "fatal: cherry-pick failed\n")
        return 128
    if in_progress:
        err("error: cherry-pick is already in progress\n"
            "hint: try \"git cherry-pick (--continue | --abort | --quit)\"\n"
            "fatal: cherry-pick failed\n")
        return 128
    if not revs:
        raise GitError("empty commit set passed")
    oids = []
    for r in revs:
        try:
            oids.append(revparse.peel(repo, revparse.resolve(repo, r), b"commit"))
        except GitError:
            raise GitError(f"bad revision '{r}'")
    head, _ = refs.resolve("HEAD")
    if head:
        _write(repo, "ORIG_HEAD", (head + "\n").encode())
    return run_picks(repo, oids, record_origin, no_commit, head)


def continue_commit(repo) -> int:
    """Commit the resolved pick: the original author, the MERGE_MSG message
    with comment lines stripped."""
    oid = (repo.gitdir / "CHERRY_PICK_HEAD").read_text().strip()
    original = Commit.parse(repo.odb.read(oid)[1])
    raw = (repo.gitdir / "MERGE_MSG").read_bytes() if (repo.gitdir / "MERGE_MSG").is_file() else original.message
    head, _ = Refs(repo).resolve("HEAD")
    tree = Index.read(repo).write_tree(repo.odb)
    msg = cleanup_message(raw, "strip")
    _commit(repo, head, tree, original, msg)
    _remove(repo, "CHERRY_PICK_HEAD", "MERGE_MSG")
    return 0

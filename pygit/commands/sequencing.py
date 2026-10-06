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


def apply_commit(repo, oid: str, action: str = "cherry-pick", reverse: bool = False,
                 mainline: int | None = None):
    """Merge one commit's changes onto HEAD (base: its parent) into the
    index and work tree, printing the merge messages; `reverse` undoes the
    commit instead (base: the commit, target: its parent). Returns (merge
    result, new index, commit, short id, subject, HEAD tree); raises
    Fatal when nothing was touched."""
    from pygit import merge_ort
    from pygit.checkout import conflict_message
    from pygit.commands.merging import _config_style, apply_result
    from pygit.status import head_tree_entries
    refs = Refs(repo)
    head, _ = refs.resolve("HEAD")
    c = Commit.parse(repo.odb.read(oid)[1])
    if len(c.parents) > 1 and mainline is None:
        raise Fatal(f"error: commit {oid} is a merge but no -m option was given.\n")
    if mainline is not None and mainline > max(len(c.parents), 1):
        raise Fatal(f"error: commit {oid} does not have parent {mainline}\n")
    base_parent = c.parents[(mainline or 1) - 1] if c.parents else None
    idx = Index.read(repo)
    head_map = head_tree_entries(repo)
    index_map = {e.path: (e.mode, e.oid) for e in idx.sorted_entries()}
    if head_map != index_map:
        raise Fatal(f"error: your local changes would be overwritten by {action}.\n"
                    "hint: commit your changes or stash them to proceed.\n")
    short = revparse.short_id(repo, oid)
    subject = _subject(c)
    parent_tree = Commit.parse(repo.odb.read(base_parent)[1]).tree if base_parent else None
    head_tree = Commit.parse(repo.odb.read(head)[1]).tree if head else None
    this_label, parent_label = f"{short} ({subject})", f"parent of {short} ({subject})"
    if reverse:
        merger = merge_ort.Merger(repo, "HEAD", parent_label, this_label, style=_config_style())
        res = merger.merge_trees(c.tree, head_tree, parent_tree)
    else:
        merger = merge_ort.Merger(repo, "HEAD", this_label, parent_label, style=_config_style())
        res = merger.merge_trees(parent_tree, head_tree, c.tree)
    applied = apply_result(repo, idx, head_map, res)
    if isinstance(applied, tuple):
        local, untracked = applied
        raise Fatal(conflict_message(local, untracked, "merge", "merge"))
    for line in res.sorted_messages():
        out(line.encode() + b"\n")
    return res, applied, c, short, subject, head_tree


def pick(repo, oid: str, record_origin: bool = False, no_commit: bool = False,
         mainline: int | None = None) -> int:
    """Apply one commit on top of HEAD. Returns 0 (picked), 1 (stopped:
    conflict or empty), raising Fatal when nothing was done."""
    head, _ = Refs(repo).resolve("HEAD")
    res, applied, c, short, subject, head_tree = apply_commit(repo, oid, mainline=mainline)
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
    _commit(repo, head, tree, c.author, message)
    return 0


REVERT_HINT = CONFLICT_HINT.replace("cherry-pick", "revert")


def revert_message(subject: str, oid: str, c: Commit, mainline: int | None) -> bytes:
    """git's revert message: 'Revert "X"' (or 'Reapply "X"' when X is
    itself a revert), naming the mainline parent for merges."""
    if subject.startswith('Revert "') and not subject[len('Revert "'):].startswith('Revert "'):
        head = 'Reapply "' + subject[len('Revert "'):]
    else:
        head = f'Revert "{subject}"'
    body = f"This reverts commit {oid}"
    if len(c.parents) > 1:
        body += f", reversing\nchanges made to {c.parents[(mainline or 1) - 1]}"
    return f"{head}\n\n{body}.\n".encode()


def revert_one(repo, oid: str, no_commit: bool = False, mainline: int | None = None) -> int:
    """Undo one commit on top of HEAD (git revert). Same returns as pick."""
    from pygit.ident import ident
    head, _ = Refs(repo).resolve("HEAD")
    res, applied, c, short, subject, head_tree = apply_commit(repo, oid, "revert", reverse=True,
                                                              mainline=mainline)
    message = revert_message(subject, oid, c, mainline)
    if not res.clean:
        conflicts = sorted({p for p, s in res.entries if s})
        if not no_commit:
            _write(repo, "REVERT_HEAD", (oid + "\n").encode())
        _write(repo, "MERGE_MSG", message.rstrip(b"\n") + b"\n\n# Conflicts:\n" +
               b"".join(b"#\t" + p + b"\n" for p in conflicts))
        err(f"error: could not revert {short}... {subject}\n" + REVERT_HINT)
        return 1
    if no_commit:
        # Unlike cherry-pick -n, revert -n records REVERT_HEAD.
        _write(repo, "REVERT_HEAD", (oid + "\n").encode())
        _write(repo, "MERGE_MSG", message)
        return 0
    tree = applied.write_tree(repo.odb)
    if tree == head_tree:
        # An empty revert: git shows the status and stops.
        _write(repo, "MERGE_MSG", message)
        from pygit.commands.porcelain import long_status
        from pygit.pathspec import cwd_prefix
        from pygit.status import compute
        out(long_status(repo, compute(repo), cwd_prefix(repo)))
        return 1
    _commit(repo, head, tree, ident(repo, "author"), message, reflog="revert")
    return 0


def _commit(repo, head: str, tree: str, author, message: bytes, reflog: str = "cherry-pick",
            date_interesting: bool = True) -> str:
    """Record a picked or reverting commit with a new committer."""
    from pygit.commands.committing import commit_summary
    from pygit.ident import ident
    refs = Refs(repo)
    new = Commit(tree, [head] if head else [], author, ident(repo, "committer"),
                 cleanup_message(message, "whitespace"))
    oid = repo.odb.write(b"commit", new.serialize())
    refs.update("HEAD", oid, f"{reflog}: {_subject(new)}")
    target = refs.head_branch()
    label = target[len("refs/heads/"):].encode() if target and target.startswith("refs/heads/") \
        else b"detached HEAD"
    out(commit_summary(repo, oid, new, label, root=not head, date_interesting=date_interesting))
    return oid


# -- the sequencer: several commits, and --continue/--skip/--abort -------------------

OPS = {"pick": ("cherry-pick", "CHERRY_PICK_HEAD"), "revert": ("revert", "REVERT_HEAD")}


def _todo(repo) -> list[tuple[str, str]]:
    """(operation, commit id) entries; git writes abbreviated ids."""
    p = repo.gitdir / "sequencer" / "todo"
    if not p.is_file():
        return []
    out_ = []
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in OPS:
            out_.append((parts[0], revparse.resolve(repo, parts[1])))
    return out_


def _save_sequencer(repo, orig_head: str, remaining: list[tuple[str, str]]) -> None:
    lines = []
    for op, oid in remaining:
        c = Commit.parse(repo.odb.read(oid)[1])
        lines.append(f"{op} {revparse.short_id(repo, oid)} {_subject(c)}\n")
    head, _ = Refs(repo).resolve("HEAD")
    _write(repo, "sequencer/head", (orig_head + "\n").encode())
    _write(repo, "sequencer/todo", "".join(lines).encode())
    _write(repo, "sequencer/abort-safety", ((head or "") + "\n").encode())


def run_todo(repo, items: list[tuple[str, str]], record_origin: bool, no_commit: bool,
             orig_head: str | None, name: str, mainline: int | None = None) -> int:
    for i, (op, oid) in enumerate(items):
        try:
            if op == "revert":
                rc = revert_one(repo, oid, no_commit, mainline)
            else:
                rc = pick(repo, oid, record_origin, no_commit, mainline)
        except Fatal as e:
            err(str(e) + f"fatal: {name} failed\n")
            return 128
        if rc:
            if len(items) > 1 or (repo.gitdir / "sequencer").is_dir():
                _save_sequencer(repo, orig_head, items[i + 1:])
            return rc
    _remove(repo, "sequencer")
    return 0


def _reset_touched(repo) -> None:
    """Back to HEAD for every path the stopped operation touched."""
    from pygit.commands.merging import _abort
    _abort(repo)


def _sequencer_command(args, op: str) -> int:
    name, head_file = OPS[op]
    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    record_origin = no_commit = False
    action = None
    mainline = None
    revs = []
    it = iter(args)
    for arg in it:
        if arg in ("-m", "--mainline"):
            mainline = int(next(it))
        elif arg.startswith("--mainline="):
            mainline = int(arg.split("=", 1)[1])
        elif arg.startswith("-m") and arg[2:].isdigit():
            mainline = int(arg[2:])
        elif arg == "-x" and op == "pick":
            record_origin = True
        elif arg in ("-n", "--no-commit"):
            no_commit = True
        elif arg in ("--no-edit", "--edit", "-e"):
            pass  # pygit never opens an editor
        elif arg in ("--continue", "--abort", "--skip", "--quit"):
            action = arg[2:]
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            revs.append(arg)
    refs = Refs(repo)
    stopped = next((f for f in ("CHERRY_PICK_HEAD", "REVERT_HEAD") if (repo.gitdir / f).is_file()), None)
    in_progress = stopped is not None or (repo.gitdir / "sequencer").is_dir()
    if action:
        if not in_progress:
            err(f"error: no cherry-pick or revert in progress\nfatal: {name} failed\n")
            return 128
        orig = (repo.gitdir / "sequencer" / "head").read_text().strip() \
            if (repo.gitdir / "sequencer" / "head").is_file() else None
        todo = _todo(repo)
        if action == "quit":
            _remove(repo, "sequencer", "CHERRY_PICK_HEAD", "REVERT_HEAD")
            return 0
        if action == "abort":
            _reset_touched(repo)
            _remove(repo, "CHERRY_PICK_HEAD", "REVERT_HEAD", "MERGE_MSG")
            if orig:
                from pygit.checkout import switch_trees
                head, _ = refs.resolve("HEAD")
                switch_trees(repo, Commit.parse(repo.odb.read(head)[1]).tree,
                             Commit.parse(repo.odb.read(orig)[1]).tree, force=True)
                refs.update("HEAD", orig, f"{name}: abort")
            _remove(repo, "sequencer")
            return 0
        if action == "skip":
            _reset_touched(repo)
            _remove(repo, "CHERRY_PICK_HEAD", "REVERT_HEAD", "MERGE_MSG")
        else:  # continue
            if Index.read(repo).has_conflicts():
                err("error: Committing is not possible because you have unmerged files.\n"
                    "hint: Fix them up in the work tree, and then use 'git add/rm <file>'\n"
                    "hint: as appropriate to mark resolution and make a commit.\n"
                    f"fatal: {name} failed\n")
                return 128
            if stopped:
                rc = continue_commit(repo)
                if rc:
                    return rc
        opts = repo.gitdir / "sequencer" / "opts"
        if mainline is None and opts.is_file():
            from pygit import config as configmod
            v = configmod.Config([opts]).get("options.mainline")
            mainline = int(v) if v else None
        return run_todo(repo, todo, record_origin, no_commit, orig, name, mainline)
    if Index.read(repo).has_conflicts():
        verb = "Cherry-picking" if op == "pick" else "Reverting"
        err(f"error: {verb} is not possible because you have unmerged files.\n"
            "hint: Fix them up in the work tree, and then use 'git add/rm <file>'\n"
            "hint: as appropriate to mark resolution and make a commit.\n"
            f"fatal: {name} failed\n")
        return 128
    if in_progress:
        err(f"error: {'cherry-pick' if stopped == 'CHERRY_PICK_HEAD' else 'revert'} is already in progress\n"
            f"hint: try \"git {name} (--continue | --abort | --quit)\"\n"
            f"fatal: {name} failed\n")
        return 128
    if not revs:
        raise GitError("empty commit set passed")
    items = []
    for r in revs:
        try:
            items.append((op, revparse.peel(repo, revparse.resolve(repo, r), b"commit")))
        except GitError:
            raise GitError(f"bad revision '{r}'")
    head, _ = refs.resolve("HEAD")
    if head:
        _write(repo, "ORIG_HEAD", (head + "\n").encode())
    rc = run_todo(repo, items, record_origin, no_commit, head, name, mainline)
    if rc and mainline is not None and (repo.gitdir / "sequencer").is_dir():
        # Remembered for --continue, in git's sequencer/opts format.
        _write(repo, "sequencer/opts", f"[options]\n\tmainline = {mainline}\n".encode())
    return rc


@command("cherry-pick")
def cmd_cherry_pick(args):
    return _sequencer_command(args, "pick")


@command("revert")
def cmd_revert(args):
    return _sequencer_command(args, "revert")


def continue_commit(repo) -> int:
    """Commit the resolved pick or revert: MERGE_MSG with comment lines
    stripped; a pick keeps its original author, a revert gets the user."""
    from pygit.ident import ident
    pick_head = repo.gitdir / "CHERRY_PICK_HEAD"
    if pick_head.is_file():
        original = Commit.parse(repo.odb.read(pick_head.read_text().strip())[1])
        author, reflog, default = original.author, "cherry-pick", original.message
    else:
        author, reflog, default = ident(repo, "author"), "revert", b""
    raw = (repo.gitdir / "MERGE_MSG").read_bytes() if (repo.gitdir / "MERGE_MSG").is_file() else default
    head, _ = Refs(repo).resolve("HEAD")
    tree = Index.read(repo).write_tree(repo.odb)
    if head and tree == Commit.parse(repo.odb.read(head)[1]).tree:
        # Nothing to commit: git shows the status and stops.
        from pygit.commands.porcelain import long_status
        from pygit.pathspec import cwd_prefix
        from pygit.status import compute
        out(long_status(repo, compute(repo), cwd_prefix(repo)))
        return 1
    _commit(repo, head, tree, author, cleanup_message(raw, "strip"), reflog=reflog,
            date_interesting=pick_head.is_file())
    _remove(repo, "CHERRY_PICK_HEAD", "REVERT_HEAD", "MERGE_MSG")
    return 0



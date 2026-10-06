"""rebase (non-interactive): replaying a branch's commits onto another,
with the `.git/rebase-merge` state git's merge backend keeps, so either
tool can continue or abort the other's rebase."""

from __future__ import annotations

import shutil

from pygit import revparse
from pygit.cli import command, err, out
from pygit.errors import GitError
from pygit.history import is_ancestor, walk
from pygit.index import Index
from pygit.objects import Commit, Signature
from pygit.pretty import cleanup_message, split_message
from pygit.refs import Refs
from pygit.repo import find_repo

STATE = "rebase-merge"

CONFLICT_HINT = (
    "hint: Resolve all conflicts manually, mark them as resolved with\n"
    "hint: \"git add/rm <conflicted_files>\", then run \"git rebase --continue\".\n"
    "hint: You can instead skip this commit: run \"git rebase --skip\".\n"
    "hint: To abort and get back to the state before \"git rebase\", run \"git rebase --abort\".\n"
    "hint: Disable this message with \"git config set advice.mergeConflict false\"\n")


def _state(repo):
    return repo.gitdir / STATE


def _read(repo, name: str) -> str:
    return (_state(repo) / name).read_text(encoding="utf-8").strip()


def _write(repo, name: str, data: str) -> None:
    p = _state(repo) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding="utf-8", newline="\n")


def _relative(p) -> str:
    import os
    try:
        return os.path.relpath(p).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def in_progress(repo) -> bool:
    return _state(repo).is_dir()


def _subject(c: Commit) -> str:
    return split_message(c.message)[0].decode("utf-8", "replace")


def _todo_lines(repo, name: str) -> list[str]:
    p = _state(repo) / name
    if not p.is_file():
        return []
    return [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def status_lines(repo, st) -> tuple[bytes, list[bytes]] | None:
    """(branch line, state paragraph) for `git status` during a rebase."""
    if not in_progress(repo):
        return None
    onto = revparse.short_id(repo, _read(repo, "onto"))

    def abbrev(line: str) -> str:
        parts = line.split(" ", 2)
        if len(parts) >= 2 and len(parts[1]) == 40:
            parts[1] = revparse.short_id(repo, parts[1])
        return " ".join(parts)

    done = [abbrev(l) for l in _todo_lines(repo, "done")]
    todo = [abbrev(l) for l in _todo_lines(repo, "git-rebase-todo")]
    lines = []
    if not done:
        lines.append("No commands done.")
    else:
        n = len(done)
        lines.append(f"Last command done ({n} command done):" if n == 1 else f"Last commands done ({n} commands done):")
        for l in done[-2:]:
            lines.append("   " + l)
        if n > 2:
            lines.append("  (see more in file .git/rebase-merge/done)")
    if not todo:
        lines.append("No commands remaining.")
    else:
        n = len(todo)
        lines.append(f"Next command to do ({n} remaining command):" if n == 1
                     else f"Next commands to do ({n} remaining commands):")
        for l in todo[:2]:
            lines.append("   " + l)
        lines.append('  (use "git rebase --edit-todo" to view and edit)')
    head_name = _read(repo, "head-name")
    branch = head_name[len("refs/heads/"):] if head_name.startswith("refs/heads/") else None
    where = f"branch '{branch}' on '{onto}'" if branch else None
    if st.unmerged or (repo.gitdir / "MERGE_MSG").is_file():
        lines.append(f"You are currently rebasing {where}." if where else "You are currently rebasing.")
        if st.unmerged:
            lines += ['  (fix conflicts and then run "git rebase --continue")',
                      '  (use "git rebase --skip" to skip this patch)',
                      '  (use "git rebase --abort" to check out the original branch)']
        else:
            lines.append('  (all conflicts fixed: run "git rebase --continue")')
    else:
        # Stopped without a conflict (an edit stop, or an unusable todo).
        lines.append(f"You are currently editing a commit while rebasing {where}." if where
                     else "You are currently editing a commit during a rebase.")
        lines += ['  (use "git commit --amend" to amend the current commit)',
                  '  (use "git rebase --continue" once you are satisfied with your changes)']
    return (f"interactive rebase in progress; onto {onto}\n".encode(),
            [(l + "\n").encode() for l in lines] + [b"\n"])


def _author_script(c: Commit) -> str:
    def q(b: bytes) -> str:
        return "'" + b.decode("utf-8", "replace").replace("'", "'\\''") + "'"
    a = c.author
    return (f"GIT_AUTHOR_NAME={q(a.name)}\nGIT_AUTHOR_EMAIL={q(a.email)}\n"
            f"GIT_AUTHOR_DATE='@{a.timestamp} {a.tz.decode()}'\n")


def _read_author(repo) -> Signature:
    vals = {}
    for line in (_state(repo) / "author-script").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        vals[k] = v.strip("'").replace("'\\''", "'")
    ts, tz = vals["GIT_AUTHOR_DATE"].lstrip("@").split()
    return Signature(vals["GIT_AUTHOR_NAME"].encode(), vals["GIT_AUTHOR_EMAIL"].encode(), int(ts), tz.encode())


def _commit(repo, tree: str, author: Signature, message: bytes, reflog: str) -> str:
    from pygit.ident import ident
    refs = Refs(repo)
    head, _ = refs.resolve("HEAD")
    c = Commit(tree, [head], author, ident(repo, "committer"), message)
    oid = repo.odb.write(b"commit", c.serialize())
    refs.update("HEAD", oid, reflog, no_deref=True)
    return oid


def _finish(repo) -> int:
    refs = Refs(repo)
    head, _ = refs.resolve("HEAD")
    head_name = _read(repo, "head-name")
    onto = _read(repo, "onto")
    if head_name.startswith("refs/"):
        refs.update(head_name, head, f"rebase (finish): {head_name} onto {onto}", no_deref=True)
        refs.set_symbolic("HEAD", head_name, f"rebase (finish): returning to {head_name}")
        err(f"Successfully rebased and updated {head_name}.\n")
    else:
        err("Successfully rebased and updated detached HEAD.\n")
    _cleanup(repo)
    return 0


def _cleanup(repo) -> None:
    shutil.rmtree(_state(repo), ignore_errors=True)
    for name in ("REBASE_HEAD", "MERGE_MSG", "AUTO_MERGE"):
        p = repo.gitdir / name
        if p.exists():
            p.unlink()


def _run(repo) -> int:
    from pygit.commands.sequencing import Fatal, apply_commit
    while True:
        todo = _todo_lines(repo, "git-rebase-todo")
        if not todo:
            return _finish(repo)
        line, rest = todo[0], todo[1:]
        _write(repo, "git-rebase-todo", "".join(l + "\n" for l in rest))
        done = (_state(repo) / "done")
        with open(done, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        msgnum = int(_read(repo, "msgnum") or 0) + 1
        _write(repo, "msgnum", f"{msgnum}\n")
        end = int(_read(repo, "end"))
        err(f"Rebasing ({msgnum}/{end})\r")
        cmd = COMMANDS.get(line.split()[0], line.split()[0])
        oid = revparse.resolve(repo, line.split()[1])
        try:
            res, applied, c, short, subject, head_tree = apply_commit(repo, oid, "rebase")
        except Fatal as e:
            err(str(e))
            return 1
        refs = Refs(repo)
        head, _ = refs.resolve("HEAD")
        hc = Commit.parse(repo.odb.read(head)[1])
        # fixup/squash fold into HEAD: its author, and its message (fixup) or
        # both messages (squash, as the combination template after cleanup).
        if cmd == "fixup":
            message, author = hc.message, hc.author
        elif cmd == "squash":
            message = hc.message.rstrip(b"\n") + b"\n\n" + c.message
            author = hc.author
        else:
            message, author = c.message, c.author
        if not res.clean:
            conflicts = sorted({p for p, s in res.entries if s})
            msg = message.rstrip(b"\n") + b"\n\n# Conflicts:\n" + b"".join(b"#\t" + p + b"\n" for p in conflicts)
            _write(repo, "message", msg.decode("utf-8", "replace"))
            (repo.gitdir / "MERGE_MSG").write_bytes(msg)
            _write(repo, "author-script", _author_script(Commit(c.tree, [], author, c.committer, b"")))
            _write(repo, "stopped-sha", oid + "\n")
            if cmd in ("fixup", "squash"):
                _write(repo, "amend-type", cmd + "\n")
            (repo.gitdir / "REBASE_HEAD").write_text(oid + "\n", encoding="utf-8")
            err(f"error: could not apply {short}... {subject}\n" + CONFLICT_HINT +
                f"Could not apply {short}... # {subject}\n")
            return 1
        tree = applied.write_tree(repo.odb)
        if cmd in ("fixup", "squash"):
            new = _amend(repo, hc, tree, author, cleanup_message(message, "strip"),
                         f"rebase ({cmd}): {_subject(hc)}")
            if cmd == "squash":
                # git runs `git commit` (with the editor) for a squash, which
                # prints its summary; a fixup is silent.
                from pygit.commands.committing import commit_summary
                out(commit_summary(repo, new, Commit.parse(repo.odb.read(new)[1]), b"detached HEAD",
                                   root=False, date_interesting=True))
            continue
        if tree == head_tree:
            continue  # now empty: dropped, as --empty=drop does
        _commit(repo, tree, author, message, f"rebase (pick): {subject}")


COMMANDS = {"p": "pick", "pick": "pick", "f": "fixup", "fixup": "fixup", "s": "squash",
            "squash": "squash", "d": "drop", "drop": "drop"}

TODO_HELP = """
# Commands:
# p, pick <commit> = use commit
# r, reword <commit> = use commit, but edit the commit message
# e, edit <commit> = use commit, but stop for amending
# s, squash <commit> = use commit, but meld into previous commit
# f, fixup [-C | -c] <commit> = like "squash" but keep only the previous
#                    commit's log message, unless -C is used, in which case
#                    keep only this commit's message; -c is same as -C but
#                    opens the editor
# x, exec <command> = run command (the rest of the line) using shell
# b, break = stop here (continue rebase later with 'git rebase --continue')
# d, drop <commit> = remove commit
# l, label <label> = label current HEAD with a name
# t, reset <label> = reset HEAD to a label
# m, merge [-C <commit> | -c <commit>] <label> [# <oneline>]
#         create a merge commit using the original merge commit's
#         message (or the oneline, if no original merge commit was
#         specified); use -c <commit> to reword the commit message
# u, update-ref <ref> = track a placeholder for the <ref> to be updated
#                       to this position in the new commits. The <ref> is
#                       updated at the end of the rebase
#
# These lines can be re-ordered; they are executed from top to bottom.
#
# If you remove a line here THAT COMMIT WILL BE LOST.
#
# However, if you remove everything, the rebase will be aborted.
#
"""


def _amend(repo, hc: Commit, tree: str, author: Signature, message: bytes, reflog: str) -> str:
    from pygit.ident import ident
    c = Commit(tree, list(hc.parents), author, ident(repo, "committer"), message)
    oid = repo.odb.write(b"commit", c.serialize())
    Refs(repo).update("HEAD", oid, reflog, no_deref=True)
    return oid


def _sequence_editor(repo) -> str | None:
    import os
    return os.environ.get("GIT_SEQUENCE_EDITOR") or repo.config.get("sequence.editor")


def _edit_todo(repo, editor: str) -> None:
    """Run the sequence editor on git-rebase-todo, as git does (through sh)."""
    import shutil as sh
    import subprocess
    shell = sh.which("sh") or sh.which("bash")
    path = str(_state(repo) / "git-rebase-todo")
    if shell:
        argv = [shell, "-c", editor + ' "$@"', editor, path]
    else:
        argv = [editor, path]
    rc = subprocess.call(argv, cwd=str(repo.worktree or repo.gitdir))
    if rc:
        raise GitError(f"there was a problem with the editor '{editor}'")


def _start(repo, upstream: str, onto_spec: str | None, branch_arg: str | None,
           interactive: bool = False) -> int:
    from pygit.checkout import switch_trees
    from pygit.status import compute
    refs = Refs(repo)
    if branch_arg:
        from pygit.commands.branching import cmd_checkout
        rc = cmd_checkout(["-q", branch_arg])
        if rc:
            return rc
    head, _ = refs.resolve("HEAD")
    head_name = refs.head_branch() or "detached HEAD"
    st = compute(repo, untracked_mode="no", detect_renames=False)
    if st.unstaged or st.unmerged:
        err("error: cannot rebase: You have unstaged changes.\nerror: Please commit or stash them.\n")
        return 1
    if st.staged:
        err("error: cannot rebase: Your index contains uncommitted changes.\nerror: Please commit or stash them.\n")
        return 1
    try:
        up = revparse.peel(repo, revparse.resolve(repo, upstream), b"commit")
    except GitError:
        raise GitError(f"invalid upstream '{upstream}'")
    onto = revparse.peel(repo, revparse.resolve(repo, onto_spec), b"commit") if onto_spec else up
    short_branch = head_name[len("refs/heads/"):] if head_name.startswith("refs/heads/") else "HEAD"
    if not interactive and onto == up and is_ancestor(repo, up, head):
        commits = list(walk(repo, [head], [up]))
        if not any(len(Commit.parse(repo.odb.read(o)[1]).parents) > 1 for o in commits):
            out(f"Current branch {short_branch} is up to date.\n".encode())
            return 0
    head_c = Commit.parse(repo.odb.read(head)[1])
    onto_c = Commit.parse(repo.odb.read(onto)[1])
    from pygit.lockfile import write_locked
    write_locked(repo.gitdir / "ORIG_HEAD", (head + "\n").encode())
    if is_ancestor(repo, head, onto) and onto == up and head != onto:
        # Fast-forward.
        switch_trees(repo, head_c.tree, onto_c.tree)
        refs.update("HEAD", onto, f"rebase (start): checkout {upstream}", no_deref=True)
        _state(repo).mkdir(parents=True, exist_ok=True)
        _write(repo, "head-name", head_name + "\n")
        _write(repo, "onto", onto + "\n")
        return _finish(repo)
    picks = [o for o in reversed(list(walk(repo, [head], [up])))
             if len(Commit.parse(repo.odb.read(o)[1]).parents) <= 1]
    # Commits whose change is already upstream (same patch id) are skipped.
    from pygit.patchid import patch_id
    upstream_ids = {patch_id(repo, o) for o in walk(repo, [up], [head])} - {None}
    if upstream_ids:
        skipped = [o for o in picks if patch_id(repo, o) in upstream_ids]
        if skipped:
            for o in skipped:
                err(f"warning: skipped previously applied commit {revparse.short_id(repo, o)}\n")
            err("hint: use --reapply-cherry-picks to include skipped commits\n"
                "hint: Disable this message with \"git config set advice.skippedCherryPicks false\"\n")
            picks = [o for o in picks if o not in skipped]
    _state(repo).mkdir(parents=True, exist_ok=True)
    _write(repo, "head-name", head_name + "\n")
    _write(repo, "onto", onto + "\n")
    _write(repo, "orig-head", head + "\n")
    _write(repo, "interactive", "")
    _write(repo, "msgnum", "0\n")
    _write(repo, "done", "")
    commands = [("pick", o) for o in picks]
    # git detaches at onto first; the todo editor runs after that.
    switch_trees(repo, head_c.tree, onto_c.tree)
    refs.update("HEAD", onto, f"rebase (start): checkout {onto_spec or upstream}", no_deref=True)
    if interactive:
        try:
            commands = _interactive_todo(repo, picks, up, head, onto)
        except _KeepState:
            return 1
        if commands is None:
            # Nothing to do: back where the rebase started.
            switch_trees(repo, onto_c.tree, head_c.tree)
            if head_name.startswith("refs/"):
                refs.set_symbolic("HEAD", head_name, f"rebase (abort): returning to {head_name}")
            else:
                refs.update("HEAD", head, "rebase (abort): returning to detached HEAD", no_deref=True)
            _cleanup(repo)
            return 1
        # skip_unnecessary_picks: leading picks already on top of onto stay
        # as they are; HEAD fast-forwards over them.
        new_onto = onto
        while commands and commands[0][0] == "pick" and \
                Commit.parse(repo.odb.read(commands[0][1])[1]).parents[:1] == [new_onto]:
            new_onto = commands.pop(0)[1]
        if new_onto != onto:
            switch_trees(repo, onto_c.tree, Commit.parse(repo.odb.read(new_onto)[1]).tree)
            refs.update("HEAD", new_onto, f"rebase: fast-forward", no_deref=True)
    _write(repo, "end", f"{len(commands)}\n")
    todo = "".join(f"{cmd} {o} # {_subject(Commit.parse(repo.odb.read(o)[1]))}\n" for cmd, o in commands)
    _write(repo, "git-rebase-todo", todo)
    return _run(repo)


def _interactive_todo(repo, picks, up, head, onto):
    """Write the todo list git shows, let the sequence editor change it,
    and read it back as (command, full id) pairs; None means abort."""
    editor = _sequence_editor(repo)
    if not editor:
        raise GitError("pygit does not open an editor: set GIT_SEQUENCE_EDITOR (or sequence.editor) "
                       "to a command that edits the todo file")
    n = len(picks)
    text = "".join(f"pick {revparse.short_id(repo, o)} # {_subject(Commit.parse(repo.odb.read(o)[1]))}\n"
                   for o in picks)
    text += (f"\n# Rebase {revparse.short_id(repo, up)}..{revparse.short_id(repo, head)} onto "
             f"{revparse.short_id(repo, onto)} ({n} command{'s' if n != 1 else ''})\n#" + TODO_HELP)
    _write(repo, "git-rebase-todo", text)
    _write(repo, "git-rebase-todo.backup", text)
    _edit_todo(repo, editor)
    commands = []
    for i, line in enumerate(_todo_lines(repo, "git-rebase-todo"), 1):
        parts = line.split()
        cmd = COMMANDS.get(parts[0])
        if cmd is None or len(parts) < 2:
            raise GitError(f"invalid line {i}: {line}")
        if cmd == "drop":
            continue
        commands.append((cmd, revparse.resolve(repo, parts[1])))
    if not commands:
        err("error: nothing to do\n")
        return None
    if commands[0][0] in ("fixup", "squash"):
        # git leaves the rebase in progress for --edit-todo or --abort.
        err(f"error: cannot '{commands[0][0]}' without a previous commit\n"
            "You can fix this with 'git rebase --edit-todo' and then run 'git rebase --continue'.\n"
            "Or you can abort the rebase with 'git rebase --abort'.\n")
        raise _KeepState()
    return commands


class _KeepState(Exception):
    """The todo is unusable; the rebase stays in progress at the original HEAD."""


@command("rebase")
def cmd_rebase(args):
    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    action = None
    onto = None
    interactive = False
    pos = []
    it = iter(args)
    for arg in it:
        if arg in ("--continue", "--abort", "--skip", "--quit"):
            action = arg[2:]
        elif arg in ("-i", "--interactive"):
            interactive = True
        elif arg == "--onto":
            onto = next(it)
        elif arg.startswith("--onto="):
            onto = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            raise GitError(f"unsupported rebase option '{arg}'")
        else:
            pos.append(arg)
    refs = Refs(repo)
    if action:
        if not in_progress(repo):
            raise GitError("no rebase in progress")
        if action == "quit":
            _cleanup(repo)
            return 0
        if action == "abort":
            from pygit.checkout import switch_trees
            head, _ = refs.resolve("HEAD")
            orig = _read(repo, "orig-head")
            head_name = _read(repo, "head-name")
            switch_trees(repo, Commit.parse(repo.odb.read(head)[1]).tree,
                         Commit.parse(repo.odb.read(orig)[1]).tree, force=True)
            if head_name.startswith("refs/"):
                refs.set_symbolic("HEAD", head_name, f"rebase (abort): returning to {head_name}")
            else:
                refs.update("HEAD", orig, "rebase (abort): returning to detached HEAD", no_deref=True)
            _cleanup(repo)
            return 0
        if action == "skip":
            from pygit.commands.merging import _abort
            _abort(repo)
            for name in ("REBASE_HEAD", "MERGE_MSG"):
                p = repo.gitdir / name
                if p.exists():
                    p.unlink()
            return _run(repo)
        # continue
        idx = Index.read(repo)
        if idx.has_conflicts():
            out(b"".join(p + b": needs merge\n" for p in idx.conflicted_paths()) +
                b"You must edit all merge conflicts and then\n"
                b"mark them as resolved using git add\n")
            return 1
        if (_state(repo) / "stopped-sha").is_file():
            head, _ = refs.resolve("HEAD")
            tree = idx.write_tree(repo.odb)
            head_tree = Commit.parse(repo.odb.read(head)[1]).tree
            amend = (_state(repo) / "amend-type").is_file()
            if tree != head_tree or amend:
                author = _read_author(repo)
                msg = cleanup_message((_state(repo) / "message").read_bytes(), "strip")
                subj = split_message(msg)[0].decode("utf-8", "replace")
                if amend:
                    # A stopped fixup/squash folds into HEAD.
                    oid = _amend(repo, Commit.parse(repo.odb.read(head)[1]), tree, author, msg,
                                 f"rebase (continue): {subj}")
                else:
                    oid = _commit(repo, tree, author, msg, f"rebase (continue): {subj}")
                from pygit.commands.committing import commit_summary
                c = Commit.parse(repo.odb.read(oid)[1])
                out(commit_summary(repo, oid, c, b"detached HEAD", root=False, date_interesting=False))
            for name in ("stopped-sha", "author-script", "message", "amend-type"):
                p = _state(repo) / name
                if p.exists():
                    p.unlink()
            for name in ("REBASE_HEAD", "MERGE_MSG"):
                p = repo.gitdir / name
                if p.exists():
                    p.unlink()
        return _run(repo)
    if in_progress(repo):
        err("fatal: It seems that there is already a rebase-merge directory, and\n"
            "I wonder if you are in the middle of another rebase.  If that is the\n"
            "case, please try\n"
            "\tgit rebase (--continue | --abort | --skip)\n"
            "If that is not the case, please\n"
            f"\trm -fr \"{_relative(_state(repo))}\"\n"
            "and run me again.  I am stopping in case you still have something\n"
            "valuable there.\n\n")
        return 128
    if not pos:
        from pygit.branching import current_branch, upstream as upstream_of
        b = current_branch(repo)
        up = upstream_of(repo, b) if b else None
        if not up:
            raise GitError("There is no tracking information for the current branch.")
        pos = [up[1]]
    return _start(repo, pos[0], onto, pos[1] if len(pos) > 1 else None, interactive)

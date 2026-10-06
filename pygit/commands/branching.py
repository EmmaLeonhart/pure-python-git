"""Stage 3 commands: branch, tag, checkout, switch, reset."""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

from pygit import config as configmod
from pygit import revparse, worktree
from pygit.branching import current_branch, tracking_info, upstream
from pygit.checkout import CheckoutConflict, switch_trees, write_file
from pygit.cli import command, err, out, parser
from pygit.errors import GitError
from pygit.history import ancestors, is_ancestor
from pygit.ident import ident
from pygit.index import Index, IndexEntry
from pygit.objects import Commit, Tag
from pygit.pathspec import Pathspec, cwd_prefix, relative_to_cwd
from pygit.pretty import cleanup_message, split_message
from pygit.refs import Refs, check_ref_format
from pygit.repo import find_repo


def _commit_of(repo, spec: str) -> str:
    return revparse.peel(repo, revparse.resolve(repo, spec), b"commit")


def _subject(repo, oid: str) -> bytes:
    return split_message(Commit.parse(repo.odb.read(oid)[1]).message)[0]


def _worktree_path(repo) -> str:
    return str(repo.worktree or repo.gitdir).replace("\\", "/")


# -- branch -------------------------------------------------------------------------

def _valid_branch_name(name: str) -> bool:
    return check_ref_format("refs/heads/" + name) and not name.startswith("-") and name != "HEAD"


def _invalid_branch(name: str) -> GitError:
    return GitError(f"'{name}' is not a valid branch name\n"
                    "hint: See 'git help check-ref-format'\n"
                    "hint: Disable this message with \"git config set advice.refSyntax false\"")


def _setup_tracking(repo, branch: str, start: str, quiet: bool = False) -> None:
    """branch.<name>.remote/merge when starting from a remote-tracking branch."""
    full = None
    got = revparse.resolve_ref_name(repo, start)
    if got:
        full = got[1]
    if not full or not full.startswith("refs/remotes/"):
        return
    rest = full[len("refs/remotes/"):]
    remote, _, name = rest.partition("/")
    if not name or name == "HEAD":
        return
    cfg = repo.gitdir / "config"
    configmod.set_value(cfg, f"branch.{branch}.remote", remote)
    configmod.set_value(cfg, f"branch.{branch}.merge", f"refs/heads/{name}")
    repo.reload_config()
    if not quiet:
        out(f"branch '{branch}' set up to track '{remote}/{name}'.\n".encode())


def create_branch(repo, name: str, start: str, force: bool = False, track: bool = True,
                  quiet: bool = False, allow_current: bool = False) -> str:
    """`allow_current`: `checkout -B` may reset the branch it is on."""
    if not _valid_branch_name(name):
        raise _invalid_branch(name)
    refs = Refs(repo)
    ref = "refs/heads/" + name
    if refs.exists(ref) and not force:
        raise GitError(f"a branch named '{name}' already exists")
    if force and not allow_current and refs.head_branch() == ref:
        raise GitError(f"cannot force update the branch '{name}' used by worktree at '{_worktree_path(repo)}'")
    try:
        oid = _commit_of(repo, start)
    except GitError:
        raise GitError(f"not a valid object name: '{start}'")
    verb = "Reset" if refs.exists(ref) else "Created"
    refs.update(ref, oid, f"branch: {verb} from {start}" if verb == "Created" else f"branch: Reset to {start}")
    if track:
        _setup_tracking(repo, name, start, quiet)
    return oid


def _branch_merged_into(repo, oid: str, branch: str) -> bool:
    """git branch -d: merged into its upstream if it has one, else into HEAD."""
    refs = Refs(repo)
    up = upstream(repo, branch)
    target = None
    if up:
        target, _ = refs.resolve(up[1])
    if target is None:
        target, _ = refs.resolve("HEAD")
    return target is not None and is_ancestor(repo, oid, target)


@command("branch")
def cmd_branch(args):
    repo = find_repo()
    refs = Refs(repo)
    mode = "list"
    force = False
    verbose = 0
    show_all = remotes = False
    quiet = False
    track = True
    upstream_arg = None
    names = []
    patterns = []
    it = iter(args)
    for arg in it:
        if arg in ("-d", "--delete"):
            mode = "delete"
        elif arg == "-D":
            mode, force = "delete", True
        elif arg in ("-m", "--move"):
            mode = "move"
        elif arg == "-M":
            mode, force = "move", True
        elif arg in ("-c", "--copy"):
            mode = "copy"
        elif arg == "-C":
            mode, force = "copy", True
        elif arg in ("-f", "--force"):
            force = True
        elif arg == "-v" or arg == "--verbose":
            verbose += 1
        elif arg == "-vv":
            verbose += 2
        elif arg in ("-a", "--all"):
            show_all = True
        elif arg in ("-r", "--remotes"):
            remotes = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg == "--show-current":
            mode = "show-current"
        elif arg == "--no-track":
            track = False
        elif arg in ("-t", "--track"):
            track = True
        elif arg.startswith("--set-upstream-to="):
            mode, upstream_arg = "upstream", arg.split("=", 1)[1]
        elif arg == "-u" or arg == "--set-upstream-to":
            mode, upstream_arg = "upstream", next(it)
        elif arg == "--unset-upstream":
            mode = "unset-upstream"
        elif arg in ("-l", "--list"):
            mode = "list"
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            names.append(arg)
    if mode == "list" and names:
        if any(c in "".join(names) for c in "*?[") or "--list" in args or "-l" in args:
            patterns = names
            names = []
        else:
            mode = "create"
    if mode == "show-current":
        b = current_branch(repo)
        if b:
            out(b.encode() + b"\n")
        return 0
    if mode == "list":
        return _list_branches(repo, verbose, show_all, remotes, patterns)
    if mode == "create":
        if len(names) > 2:
            raise GitError("too many arguments")
        start = names[1] if len(names) > 1 else "HEAD"
        if start == "HEAD" and refs.resolve("HEAD")[0] is None:
            raise GitError(f"not a valid object name: '{current_branch(repo) or 'HEAD'}'")
        create_branch(repo, names[0], start, force=force, track=track, quiet=quiet)
        return 0
    if mode == "delete":
        if not names:
            raise GitError("branch name required")
        rc = 0
        head_ref = refs.head_branch()
        for name in names:
            ref = ("refs/remotes/" if remotes else "refs/heads/") + name
            oid, _ = refs.resolve(ref)
            if oid is None:
                err(f"error: {'remote-tracking ' if remotes else ''}branch '{name}' not found\n")
                rc = 1
                continue
            if not remotes and ref == head_ref:
                err(f"error: cannot delete branch '{name}' used by worktree at '{_worktree_path(repo)}'\n")
                rc = 1
                continue
            if not force and not remotes and not _branch_merged_into(repo, oid, name):
                err(f"error: the branch '{name}' is not fully merged\n"
                    f"hint: If you are sure you want to delete it, run 'git branch -D {name}'\n"
                    "hint: Disable this message with \"git config set advice.forceDeleteBranch false\"\n")
                rc = 1
                continue
            refs.delete(ref)
            if not remotes:
                _remove_config_section(repo, f'branch "{name}"')
            if not quiet:
                short = revparse.short_id(repo, oid)
                kind = "remote-tracking branch" if remotes else "branch"
                out(f"Deleted {kind} {name} (was {short}).\n".encode())
        return rc
    if mode in ("move", "copy"):
        if len(names) == 1:
            old, new = current_branch(repo), names[0]
            if old is None:
                raise GitError("cannot rename the current branch while not on any")
        elif len(names) == 2:
            old, new = names
        else:
            raise GitError("too many arguments for a rename operation")
        old_ref, new_ref = "refs/heads/" + old, "refs/heads/" + new
        oid, _ = refs.resolve(old_ref)
        if oid is None:
            if refs.head_branch() == old_ref:
                raise GitError(f"no commit on branch '{old}' yet")
            raise GitError(f"no branch named '{old}'")
        if not _valid_branch_name(new):
            raise _invalid_branch(new)
        if refs.exists(new_ref) and not force and new_ref != old_ref:
            raise GitError(f"a branch named '{new}' already exists")
        log_old = repo.gitdir / "logs" / old_ref
        log_data = log_old.read_bytes() if log_old.is_file() else b""
        verb = "renamed" if mode == "move" else "copied"
        if mode == "move":
            refs.delete(old_ref)
        lp = repo.gitdir / "logs" / new_ref
        if log_data:
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_bytes(log_data)
        refs.update(new_ref, oid, f"Branch: {verb} {old_ref} to {new_ref}")
        if mode == "move" and refs.head_branch() == old_ref:
            refs.set_symbolic("HEAD", new_ref)
        _copy_config_section(repo, f'branch "{old}"', f'branch "{new}"', move=(mode == "move"))
        return 0
    if mode == "upstream":
        branch = names[0] if names else current_branch(repo)
        if branch is None:
            raise GitError("could not set upstream of HEAD to " + upstream_arg + " when it does not point to any branch")
        got = revparse.resolve_ref_name(repo, upstream_arg)
        if not got:
            raise GitError(f"the requested upstream branch '{upstream_arg}' does not exist\n"
                           "hint:\n"
                           "hint: If you are planning on basing your work on an upstream\n"
                           "hint: branch that already exists at the remote, you may need to\n"
                           "hint: run \"git fetch\" to retrieve it.\n"
                           "hint:\n"
                           "hint: If you are planning to push out a new local branch that\n"
                           "hint: will track its remote counterpart, you may want to use\n"
                           "hint: \"git push -u\" to set the upstream config as you push.\n"
                           "hint: Disable this message with \"git config set advice.setUpstreamFailure false\"")
        full = got[1]
        cfg = repo.gitdir / "config"
        if full.startswith("refs/remotes/"):
            remote, _, name = full[len("refs/remotes/"):].partition("/")
            configmod.set_value(cfg, f"branch.{branch}.remote", remote)
            configmod.set_value(cfg, f"branch.{branch}.merge", f"refs/heads/{name}")
            shown = f"{remote}/{name}"
        elif full.startswith("refs/heads/"):
            configmod.set_value(cfg, f"branch.{branch}.remote", ".")
            configmod.set_value(cfg, f"branch.{branch}.merge", full)
            shown = full[len("refs/heads/"):]
        else:
            raise GitError(f"cannot set up tracking information; starting point '{upstream_arg}' is not a branch")
        if not quiet:
            out(f"branch '{branch}' set up to track '{shown}'.\n".encode())
        return 0
    if mode == "unset-upstream":
        branch = names[0] if names else current_branch(repo)
        if not repo.config.get(f"branch.{branch}.merge"):
            raise GitError(f"branch '{branch}' has no upstream information")
        _remove_keys(repo, f'branch "{branch}"', ("remote", "merge"))
        return 0
    return 0


def _list_branches(repo, verbose, show_all, remotes, patterns) -> int:
    refs = Refs(repo)
    head_ref = refs.head_branch()
    head_oid, _ = refs.resolve("HEAD")
    items = []   # (display name, ref, oid, is_current, symref_target)
    if head_ref is None and head_oid and not remotes:
        from pygit.branching import detached_description
        desc = detached_description(repo).decode()
        label = f"({desc})" if desc.startswith("HEAD detached") else "(no branch)"
        items.append((label, "HEAD", head_oid, True, None))
    if not remotes or show_all:
        for ref, oid in refs.list("refs/heads/").items():
            items.append((ref[len("refs/heads/"):], ref, oid, ref == head_ref, None))
    if remotes or show_all:
        for ref, oid in refs.list("refs/remotes/").items():
            name = ref[len("refs/remotes/"):]
            target = refs.symbolic_target(ref)
            shown = ("remotes/" + name) if show_all else name
            items.append((shown, ref, oid, False, target))
    if patterns:
        items = [i for i in items if i[1] == "HEAD" or any(fnmatch.fnmatchcase(i[0], p) for p in patterns)]
    width = max((len(i[0]) for i in items if not i[4]), default=0)
    lines = []
    for name, ref, oid, current, target in items:
        mark = "* " if current else "  "
        if target:
            t = target[len("refs/remotes/"):] if target.startswith("refs/remotes/") else target
            lines.append(f"{mark}{name} -> {t}\n".encode())
            continue
        if not verbose:
            lines.append(f"{mark}{name}\n".encode())
            continue
        short = revparse.short_id(repo, oid)
        info = ""
        if ref.startswith("refs/heads/"):
            info = _tracking_brief(repo, ref[len("refs/heads/"):], verbose)
        subj = _subject(repo, oid).decode("utf-8", "replace")
        lines.append(f"{mark}{name.ljust(width)} {short} {info}{subj}\n".encode())
    out(b"".join(lines))
    return 0


def _tracking_brief(repo, branch: str, verbose: int) -> str:
    up = upstream(repo, branch)
    if not up:
        return ""
    from pygit.history import ahead_behind
    refs = Refs(repo)
    local, _ = refs.resolve("refs/heads/" + branch)
    remote, _ = refs.resolve(up[1])
    if remote is None:
        return f"[{up[0]}: gone] " if verbose > 1 else "[gone] "
    ahead, behind = ahead_behind(repo, local, remote)
    parts = []
    if ahead:
        parts.append(f"ahead {ahead}")
    if behind:
        parts.append(f"behind {behind}")
    if verbose > 1:
        return f"[{up[0]}{': ' + ', '.join(parts) if parts else ''}] "
    return f"[{', '.join(parts)}] " if parts else ""


def _section_lines(repo):
    path = repo.gitdir / "config"
    try:
        return path, path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return path, []


def _section_matches(line: str, header: str) -> bool:
    s = line.strip()
    return s.startswith("[") and s[1:].split("]")[0].strip() == header


def _remove_config_section(repo, header: str) -> None:
    path, lines = _section_lines(repo)
    out_lines, skip = [], False
    for line in lines:
        if line.strip().startswith("["):
            skip = _section_matches(line, header)
        if not skip:
            out_lines.append(line)
    if out_lines != lines:
        path.write_text("\n".join(out_lines) + "\n", encoding="utf-8", newline="\n")
        repo.reload_config()


def _remove_keys(repo, header: str, keys) -> None:
    path, lines = _section_lines(repo)
    out_lines, inside = [], False
    for line in lines:
        if line.strip().startswith("["):
            inside = _section_matches(line, header)
        elif inside and line.strip().partition("=")[0].strip().lower() in keys:
            continue
        out_lines.append(line)
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8", newline="\n")
    repo.reload_config()


def _copy_config_section(repo, old: str, new: str, move: bool) -> None:
    path, lines = _section_lines(repo)
    body, inside = [], False
    for line in lines:
        if line.strip().startswith("["):
            inside = _section_matches(line, old)
            continue
        if inside:
            body.append(line)
    if not body:
        return
    if move:
        _remove_config_section(repo, old)
        path, lines = _section_lines(repo)
    lines.append(f"[{new}]")
    lines.extend(body)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    repo.reload_config()


# -- tag ------------------------------------------------------------------------------

@command("tag")
def cmd_tag(args):
    repo = find_repo()
    refs = Refs(repo)
    annotate = delete = force = listing = False
    lines_n = 0
    messages, msg_file = [], None
    cleanup = None
    names = []
    it = iter(args)
    for arg in it:
        if arg in ("-a", "--annotate"):
            annotate = True
        elif arg in ("-m", "--message"):
            messages.append(next(it))
            annotate = True
        elif arg.startswith("-m") and len(arg) > 2:
            messages.append(arg[2:])
            annotate = True
        elif arg in ("-F", "--file"):
            msg_file = next(it)
            annotate = True
        elif arg in ("-d", "--delete"):
            delete = True
        elif arg in ("-f", "--force"):
            force = True
        elif arg in ("-l", "--list"):
            listing = True
        elif arg.startswith("-n"):
            lines_n = int(arg[2:]) if len(arg) > 2 else 1
            listing = True
        elif arg.startswith("--cleanup="):
            cleanup = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            names.append(arg)
    if delete:
        rc = 0
        for name in names:
            oid, _ = refs.resolve("refs/tags/" + name)
            if oid is None:
                err(f"error: tag '{name}' not found.\n")
                rc = 1
                continue
            refs.delete("refs/tags/" + name)
            out(f"Deleted tag '{name}' (was {revparse.short_id(repo, oid)})\n".encode())
        return rc
    if listing or not names:
        tags = refs.list("refs/tags/")
        for ref in sorted(tags, key=lambda r: r.encode()):
            name = ref[len("refs/tags/"):]
            if names and not any(fnmatch.fnmatchcase(name, p) for p in names):
                continue
            if not lines_n:
                out(name.encode() + b"\n")
                continue
            oid = tags[ref]
            t, data = repo.odb.read(oid)
            if t == b"tag":
                msg = Tag.parse(data).message
            elif t == b"commit":
                msg = Commit.parse(data).message
            else:
                msg = b""
            body = [l for l in msg.split(b"\n")]
            while body and not body[0].strip():
                body.pop(0)
            shown = body[:lines_n]
            while shown and not shown[-1].strip():
                shown.pop()
            line = name.encode().ljust(15) + b" "
            if shown:
                line += shown[0]
                for extra in shown[1:]:
                    line += b"\n    " + extra
            out(line.rstrip(b" ") + b"\n" if not shown else line + b"\n")
        return 0
    name = names[0]
    target = names[1] if len(names) > 1 else "HEAD"
    if len(names) > 2:
        raise GitError("too many arguments")
    if not check_ref_format("refs/tags/" + name):
        raise GitError(f"'{name}' is not a valid tag name.")
    ref = "refs/tags/" + name
    old, _ = refs.resolve(ref)
    if old is not None and not force:
        raise GitError(f"tag '{name}' already exists")
    try:
        obj = revparse.resolve(repo, target)
    except GitError:
        raise GitError(f"Failed to resolve '{target}' as a valid ref.")
    if annotate:
        if messages:
            raw = b"\n\n".join(m.encode() for m in messages)
        elif msg_file:
            raw = sys.stdin.buffer.read() if msg_file == "-" else Path(msg_file).read_bytes()
        else:
            raise GitError("pygit tag needs -m or -F for an annotated tag; it does not open an editor")
        mode = cleanup or "strip"
        msg = cleanup_message(raw, "strip" if mode in ("strip", "default") else mode)
        obj_type, _ = repo.odb.read(obj)
        if obj_type == b"tag" and repo.config.get_bool("advice.nestedTag", True):
            err("hint: You have created a nested tag. The object referred to by your new tag is\n"
                "hint: already a tag. If you meant to tag the object that it points to, use:\n"
                "hint:\n"
                f"hint: \tgit tag -f {name} {target}^{{}}\n"
                "hint: Disable this message with \"git config set advice.nestedTag false\"\n")
        tag = Tag(obj, obj_type, name.encode(), ident(repo, "committer"), msg)
        obj = repo.odb.write(b"tag", tag.serialize())
    refs.update(ref, obj, "")
    if old is not None and old != obj:
        out(f"Updated tag '{name}' (was {revparse.short_id(repo, old)})\n".encode())
    return 0


# -- checkout / switch -------------------------------------------------------------------

_DETACHED_ADVICE = (
    "\n"
    "You are in 'detached HEAD' state. You can look around, make experimental\n"
    "changes and commit them, and you can discard any commits you make in this\n"
    "state without impacting any branches by switching back to a branch.\n"
    "\n"
    "If you want to create a new branch to retain commits you create, you may\n"
    "do so (now or later) by using -c with the switch command. Example:\n"
    "\n"
    "  git switch -c <new-branch-name>\n"
    "\n"
    "Or undo this operation with:\n"
    "\n"
    "  git switch -\n"
    "\n"
    "Turn off this advice by setting config variable advice.detachedHead to false\n"
    "\n")


def _head_tree(repo, oid):
    return Commit.parse(repo.odb.read(oid)[1]).tree if oid else None


def _orphaned_commits(repo, old_head: str, new_head: str) -> list[str]:
    """Commits reachable from the detached HEAD we leave but from no ref."""
    refs = Refs(repo)
    keep = [new_head] + list(refs.list("refs/").values())
    reachable = ancestors(repo, [o for o in keep if repo.odb.read(o)[0] == b"commit"])
    if old_head in reachable:
        return []
    from pygit.history import walk
    return [c for c in walk(repo, [old_head], [o for o in keep if repo.odb.read(o)[0] == b"commit"])]


def _local_changes_report(repo, new_tree: str) -> bytes:
    """`M\tpath` lines for local changes carried across a checkout."""
    from pygit.status import compute, staged_changes
    from pygit.treediff import tree_entries
    idx = Index.read(repo)
    st = compute(repo, untracked_mode="no", detect_renames=False)
    staged = staged_changes(tree_entries(repo.odb, new_tree), idx, False, repo.odb)
    codes = {}
    for c in staged:
        codes[c.path] = c.status
    for c in st.unstaged:
        codes.setdefault(c.path, c.status)
    prefix = cwd_prefix(repo)
    from pygit.quote import quote_path
    return b"".join(f"{codes[p]}\t".encode() + quote_path(relative_to_cwd(p, prefix)) + b"\n"
                    for p in sorted(codes))


def do_switch(repo, target_ref: str | None, target_oid: str, label: str, create: str | None,
              force_create: bool, detach_label: str | None, force: bool, quiet: bool,
              start_label: str | None = None, track: bool = True) -> int:
    refs = Refs(repo)
    idx = Index.read(repo)
    if idx.has_conflicts() and not force:
        err("error: you need to resolve your current index first\n" +
            "".join(f"{os.fsdecode(p)}: needs merge\n" for p in idx.conflicted_paths()))
        return 1
    old_head, _ = refs.resolve("HEAD")
    old_branch = refs.head_branch()
    old_tree = _head_tree(repo, old_head)
    new_tree = _head_tree(repo, target_oid)
    if create and refs.exists("refs/heads/" + create) and not force_create:
        raise GitError(f"a branch named '{create}' already exists")
    if create and not _valid_branch_name(create):
        raise _invalid_branch(create)
    try:
        switch_trees(repo, old_tree, new_tree, force=force)
    except CheckoutConflict as e:
        err(e.message)
        return 1
    old_desc = (old_branch[len("refs/heads/"):] if old_branch and old_branch.startswith("refs/heads/")
                else (old_head or ""))
    if create:
        create_branch(repo, create, start_label or target_oid, force=force_create, track=track, quiet=quiet,
                      allow_current=True)
        target_ref = "refs/heads/" + create
    move_msg = f"checkout: moving from {old_desc} to {create or label}"
    report = b""
    if not force:
        report = _local_changes_report(repo, new_tree)
    if old_branch is None and old_head and not quiet and old_head != target_oid:
        lost = _orphaned_commits(repo, old_head, target_oid)
        if lost:
            n = len(lost)
            shown = lost[:5] if n <= 5 else lost[:4]
            lines = "".join(f"  {revparse.short_id(repo, c)} {_subject(repo, c).decode('utf-8', 'replace')}\n"
                            for c in shown)
            if n > 5:
                lines += f" ... and {n - 4} more.\n"
            msg = (f"Warning: you are leaving {n} commit{'s' if n > 1 else ''} behind, not connected to\n"
                   f"any of your branches:\n\n{lines}\n")
            if repo.config.get_bool("advice.detachedHead", True):
                msg += (f"If you want to keep {'it' if n == 1 else 'them'} by creating a new branch, "
                        f"this may be a good time\nto do so with:\n\n"
                        f" git branch <new-branch-name> {revparse.short_id(repo, old_head)}\n\n")
            err(msg)
        else:
            err(f"Previous HEAD position was {revparse.short_id(repo, old_head)} "
                f"{_subject(repo, old_head).decode('utf-8', 'replace')}\n")
    if target_ref is not None:
        if old_branch == target_ref and not create:
            out(report)
            if not quiet:
                err(f"Already on '{label}'\n")
        else:
            refs.set_symbolic("HEAD", target_ref, move_msg)
            out(report)
            if not quiet:
                if create:
                    verb = "Reset" if force_create and old_branch == target_ref else "Switched to a new"
                    err(f"{verb} branch '{create}'\n")
                else:
                    err(f"Switched to branch '{label}'\n")
        info = tracking_info(repo)
        if info and not quiet:
            out(info)
    else:
        refs.update("HEAD", target_oid, move_msg, no_deref=True)
        out(report)
        if not quiet:
            if old_branch is not None or old_head != target_oid:
                advice = repo.config.get_bool("advice.detachedHead", True)
                if advice and old_branch is not None:
                    err(f"Note: switching to '{detach_label}'.\n" + _DETACHED_ADVICE)
            short = revparse.short_id(repo, target_oid)
            err(f"HEAD is now at {short} {_subject(repo, target_oid).decode('utf-8', 'replace')}\n")
    return 0


def checkout_paths(repo, source: str | None, paths: list[str], report: bool) -> int:
    """`checkout [<tree-ish>] -- <paths>`: copy files from the index or a tree."""
    idx = Index.read(repo)
    spec = Pathspec(repo, paths)
    if source is None:
        entries = [e for e in idx.sorted_entries() if spec.matches(e.path)]
        unmerged = sorted({e.path for e in entries if e.stage})
        if unmerged:
            for p in unmerged:
                err(f"error: path '{os.fsdecode(p)}' is unmerged\n")
            return 1
        matched = [False] * len(spec.items)
        for e in entries:
            n = spec.matching_item(e.path)
            if n is not None:
                matched[n] = True
        for n, ok in enumerate(matched):
            if not ok:
                err(f"error: pathspec '{spec.args[n]}' did not match any file(s) known to git\n")
                return 1
        count = 0
        for e in entries:
            st = worktree.lstat(repo, e.path)
            if st is not None and not worktree.is_modified(repo, e, st):
                count += 1
                continue
            st = write_file(repo, e.path, e.mode, e.oid)
            e.set_stat(st)
            count += 1
        idx.write()
        if report:
            err(f"Updated {count} path{'s' if count != 1 else ''} from the index\n")
        return 0
    tree_oid = revparse.peel(repo, revparse.resolve(repo, source), b"tree")
    from pygit.checkout import tree_map
    tmap = {p: v for p, v in tree_map(repo.odb, tree_oid).items() if spec.matches(p)}
    matched = [False] * len(spec.items)
    for p in tmap:
        n = spec.matching_item(p)
        if n is not None:
            matched[n] = True
    for n, ok in enumerate(matched):
        if not ok:
            err(f"error: pathspec '{spec.args[n]}' did not match any file(s) known to git\n")
            return 1
    n = 0
    for p, (mode, oid) in sorted(tmap.items()):
        old = idx.get(p)
        st = worktree.lstat(repo, p)
        # An index entry that already matches is kept (with its stat data),
        # and the file is only rewritten (and counted) if it differs.
        if old is not None and old.oid == oid and old.mode == mode \
                and not any(idx.get(p, s) for s in (1, 2, 3)) \
                and st is not None and not worktree.is_modified(repo, old, st):
            continue
        st = write_file(repo, p, mode, oid)
        e = IndexEntry(path=p, oid=oid, mode=mode)
        e.set_stat(st)
        idx.add(e)
        n += 1
    idx.write()
    if report:
        err(f"Updated {n} path{'s' if n != 1 else ''} from {revparse.short_id(repo, tree_oid)}\n")
    return 0


def _resolve_branch_or_commit(repo, name: str):
    """(ref or None, commit oid) for a checkout target."""
    refs = Refs(repo)
    if name == "-":
        prev = _previous_branch(repo)
        if prev is None:
            raise GitError("No previous branch.")  # matches 'git switch -' with no history closely enough
        name = prev
    ref = "refs/heads/" + name
    oid, _ = refs.resolve(ref)
    if oid is not None or refs.head_branch() == ref:
        return ref, oid, name
    return None, _commit_of(repo, name), name


def _previous_branch(repo) -> str | None:
    log = repo.gitdir / "logs" / "HEAD"
    try:
        lines = log.read_bytes().splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        _, _, msg = line.partition(b"\t")
        if msg.startswith(b"checkout: moving from "):
            frm = msg[len(b"checkout: moving from "):].rsplit(b" to ", 1)[0]
            return frm.decode("utf-8", "replace")
    return None


def _remote_dwim(repo, name: str):
    """`checkout foo` creating foo from the only remote that has it."""
    hits = [r for r in Refs(repo).list("refs/remotes/") if r.split("/", 3)[-1] == name and r.count("/") >= 3]
    return hits[0] if len(hits) == 1 else None


@command("checkout")
def cmd_checkout(args):
    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    create = None
    force_create = force = detach = quiet = False
    track = True
    orphan = None
    rest = []
    dashdash = None
    it = iter(args)
    for arg in it:
        if dashdash is not None:
            rest.append(arg)
            continue
        if arg == "--":
            dashdash = len(rest)
        elif arg == "-b":
            create = next(it)
        elif arg == "-B":
            create, force_create = next(it), True
        elif arg in ("-f", "--force"):
            force = True
        elif arg == "--detach":
            detach = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg == "--no-track":
            track = False
        elif arg in ("-t", "--track"):
            track = True
        elif arg == "--orphan":
            orphan = next(it)
        elif arg.startswith("-") and arg != "-":
            raise GitError(f"unknown option '{arg}'")
        else:
            rest.append(arg)
    refs = Refs(repo)
    if orphan:
        if refs.exists("refs/heads/" + orphan):
            raise GitError(f"a branch named '{orphan}' already exists")
        old_branch = refs.head_branch()
        refs.set_symbolic("HEAD", "refs/heads/" + orphan)
        err(f"Switched to a new branch '{orphan}'\n")
        return 0
    if dashdash is not None:
        source = rest[0] if dashdash == 1 else None
        if dashdash > 1:
            raise GitError("only one reference expected")
        paths = rest[dashdash:]
        return checkout_paths(repo, source, paths, report=False)
    if not rest:
        if create:
            return do_switch(repo, None, refs.resolve("HEAD")[0], "HEAD", create, force_create, None,
                             force, quiet, start_label="HEAD", track=track)
        if detach:
            return do_switch(repo, None, refs.resolve("HEAD")[0], "HEAD", None, False, "HEAD", force, quiet)
        # `git checkout` with nothing: report local changes and tracking.
        head, _ = refs.resolve("HEAD")
        out(_local_changes_report(repo, _head_tree(repo, head)))
        info = tracking_info(repo)
        if info:
            out(info)
        return 0
    first = rest[0]
    try:
        ref, oid, label = _resolve_branch_or_commit(repo, first)
    except GitError:
        remote = _remote_dwim(repo, first) if not create and len(rest) == 1 else None
        if remote:
            oid = refs.resolve(remote)[0]
            return do_switch(repo, None, oid, first, first, False, None, force, quiet,
                             start_label=remote[len("refs/remotes/"):], track=track)
        if len(rest) >= 1 and not create:
            # Paths without "--".
            return checkout_paths(repo, None, rest, report=True)
        raise GitError(f"'{first}' is not a commit and a branch '{create}' cannot be created from it")
    if len(rest) > 1:
        return checkout_paths(repo, first, rest[1:], report=True)
    if create:
        return do_switch(repo, None, oid, first, create, force_create, None, force, quiet,
                         start_label=first, track=track)
    if detach and ref is not None:
        return do_switch(repo, None, oid, label, None, False, first, force, quiet)
    return do_switch(repo, ref, oid, label, None, False, first, force, quiet)


@command("switch")
def cmd_switch(args):
    repo = find_repo()
    if repo.worktree is None:
        raise GitError("this operation must be run in a work tree")
    create = None
    force_create = force = detach = quiet = False
    track = True
    names = []
    it = iter(args)
    for arg in it:
        if arg in ("-c", "--create"):
            create = next(it)
        elif arg in ("-C", "--force-create"):
            create, force_create = next(it), True
        elif arg in ("-d", "--detach"):
            detach = True
        elif arg in ("-f", "--force", "--discard-changes"):
            force = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg == "--no-track":
            track = False
        elif arg.startswith("-") and arg != "-":
            raise GitError(f"unknown option '{arg}'")
        else:
            names.append(arg)
    refs = Refs(repo)
    if create:
        start = names[0] if names else "HEAD"
        oid = _commit_of(repo, start)
        return do_switch(repo, None, oid, start, create, force_create, None, force, quiet,
                         start_label=start, track=track)
    if not names:
        if detach:
            oid = refs.resolve("HEAD")[0]
            return do_switch(repo, None, oid, "HEAD", None, False, "HEAD", force, quiet)
        raise GitError("missing branch or commit argument")
    name = names[0]
    try:
        ref, oid, label = _resolve_branch_or_commit(repo, name)
    except GitError:
        remote = _remote_dwim(repo, name)
        if remote:
            oid = refs.resolve(remote)[0]
            return do_switch(repo, None, oid, name, name, False, None, force, quiet,
                             start_label=remote[len("refs/remotes/"):], track=track)
        raise GitError(f"invalid reference: {name}")
    if detach:
        return do_switch(repo, None, oid, label, None, False, name, force, quiet)
    if ref is None:
        raise GitError(f"a branch is expected, got commit '{name}'\n"
                       "hint: If you want to detach HEAD at the commit, try again with the --detach option.")
    return do_switch(repo, ref, oid, label, None, False, name, force, quiet)


# -- reset -----------------------------------------------------------------------------

@command("reset")
def cmd_reset(args):
    repo = find_repo()
    mode = "mixed"
    quiet = False
    revs, paths = [], []
    dashdash = False
    for arg in args:
        if dashdash:
            paths.append(arg)
        elif arg == "--":
            dashdash = True
        elif arg in ("--soft", "--mixed", "--hard", "--merge", "--keep"):
            mode = arg[2:]
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            revs.append(arg)
    refs = Refs(repo)
    if not dashdash and len(revs) > 1:
        paths, revs = revs[1:], revs[:1]
    if not dashdash and len(revs) == 1:
        try:
            revparse.resolve(repo, revs[0])
        except GitError:
            # Not a revision: a path only if it exists in the work tree.
            if repo.worktree is not None and (repo.worktree / revs[0]).exists():
                paths, revs = revs, []
            else:
                raise GitError(f"ambiguous argument '{revs[0]}': unknown revision or path not in the "
                               "working tree.\nUse '--' to separate paths from revisions, like this:\n"
                               "'git <command> [<revision>...] -- [<file>...]'")
    target_spec = revs[0] if revs else "HEAD"
    head, _ = refs.resolve("HEAD")
    if target_spec == "HEAD" and head is None:
        target = None
    else:
        try:
            target = _commit_of(repo, target_spec)
        except GitError:
            if paths:
                raise GitError(f"Failed to resolve '{target_spec}' as a valid tree.")
            raise GitError(f"ambiguous argument '{target_spec}': unknown revision or path not in the working tree.\n"
                           "Use '--' to separate paths from revisions, like this:\n"
                           "'git <command> [<revision>...] -- [<file>...]'")
    target_tree = _head_tree(repo, target)
    if paths:
        if mode != "mixed":
            raise GitError(f"Cannot do --{mode} reset with paths.")
        spec = Pathspec(repo, paths)
        from pygit.checkout import tree_map
        tmap = tree_map(repo.odb, target_tree)
        idx = Index.read(repo)
        for e in list(idx.sorted_entries()):
            if spec.matches(e.path) and e.path not in tmap:
                idx.remove(e.path)
        for p, (m, o) in tmap.items():
            if spec.matches(p):
                cur = idx.get(p)
                if cur is None or cur.oid != o or cur.mode != m or any(idx.get(p, s) for s in (1, 2, 3)):
                    idx.add(IndexEntry(path=p, oid=o, mode=m))
        idx.write()
        if not quiet:
            _report_unstaged(repo)
        return 0
    if mode == "hard" or mode in ("merge", "keep"):
        try:
            switch_trees(repo, _head_tree(repo, head), target_tree, force=(mode == "hard"),
                         action="reset", hint="reset")
        except CheckoutConflict as e:
            err(e.message)
            return 1
    if head is not None:
        from pygit.lockfile import write_locked
        write_locked(repo.gitdir / "ORIG_HEAD", (head + "\n").encode())
    if target is not None:
        refs.update("HEAD", target, f"reset: moving to {target_spec}")
    if mode == "mixed":
        old = Index.read(repo)
        idx = Index.from_tree(repo, target_tree)
        for key, e in idx.entries.items():
            o = old.entries.get(key)
            if o is not None and o.oid == e.oid and o.mode == e.mode:
                idx.entries[key] = o
        idx.write()
        if not quiet:
            _report_unstaged(repo)
    for name in ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE"):
        try:
            (repo.gitdir / name).unlink()
        except FileNotFoundError:
            pass
    if mode == "hard" and not quiet:
        out(f"HEAD is now at {revparse.short_id(repo, target)} ".encode() + _subject(repo, target) + b"\n")
    return 0


def _report_unstaged(repo) -> None:
    from pygit.status import compute
    st = compute(repo, untracked_mode="no", detect_renames=False)
    if st.unstaged:
        prefix = cwd_prefix(repo)
        from pygit.quote import quote_path
        out(b"Unstaged changes after reset:\n" +
            b"".join(c.status.encode() + b"\t" + quote_path(relative_to_cwd(c.path, prefix)) + b"\n"
                     for c in st.unstaged))

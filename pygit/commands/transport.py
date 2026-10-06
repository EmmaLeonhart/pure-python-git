"""Stage 5 commands: clone, fetch, push, pull, remote (local paths only)."""

from __future__ import annotations

import os
from pathlib import Path

from pygit import config as configmod
from pygit import revparse, transport
from pygit.cli import command, err, out
from pygit.errors import GitError
from pygit.refs import Refs
from pygit.repo import find_repo, init

SUMMARY_WIDTH = 17  # 2 * abbrev + 3


def _remote_url(repo, name: str) -> str:
    url = repo.config.get(f"remote.{name}.url")
    if url is None:
        if Path(name).exists():
            return transport.url_of(name)
        raise GitError(f"'{name}' does not appear to be a git repository\n"
                       "fatal: Could not read from remote repository.\n\n"
                       "Please make sure you have the correct access rights\n"
                       "and the repository exists.")
    return url


# -- clone -----------------------------------------------------------------------------

@command("clone")
def cmd_clone(args):
    bare = quiet = False
    origin = "origin"
    branch_opt = None
    pos = []
    it = iter(args)
    for arg in it:
        if arg == "--bare":
            bare = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg in ("-o", "--origin"):
            origin = next(it)
        elif arg in ("-b", "--branch"):
            branch_opt = next(it)
        elif arg in ("--local", "-l", "--no-hardlinks", "--no-local"):
            pass
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            pos.append(arg)
    if not pos:
        raise GitError("You must specify a repository to clone.")
    src_arg = pos[0]
    if not Path(src_arg).exists():
        raise GitError(f"repository '{src_arg}' does not exist")
    src = transport.open_repo(src_arg)
    url = transport.url_of(src.worktree if src.worktree is not None else src.gitdir)
    if len(pos) > 1:
        dst = pos[1]
    else:
        name = Path(src_arg.rstrip("/\\")).name
        if name == ".git":
            name = Path(src_arg.rstrip("/\\")).parent.name
        if name.endswith(".git"):
            name = name[:-4]
        dst = name + (".git" if bare else "")
    dpath = Path(dst)
    if dpath.exists() and any(dpath.iterdir()):
        raise GitError(f"destination path '{dst}' already exists and is not an empty directory.")
    if not quiet:
        err(f"Cloning into {'bare repository ' if bare else ''}'{dst}'...\n")
    refs = transport.advertised_refs(src)
    head_branch = branch_opt or transport.remote_head_branch(src) or "master"
    dpath.mkdir(parents=True, exist_ok=True)
    repo = init(dpath, bare=bare, initial_branch=head_branch, quiet=True)
    cfg = repo.gitdir / "config"
    configmod.set_value(cfg, f"remote.{origin}.url", url)
    if not bare:
        configmod.set_value(cfg, f"remote.{origin}.fetch", f"+refs/heads/*:refs/remotes/{origin}/*")
    repo.reload_config()
    if not refs:
        if not bare:  # git records the upstream even for an empty clone
            configmod.set_value(cfg, f"branch.{head_branch}.remote", origin)
            configmod.set_value(cfg, f"branch.{head_branch}.merge", f"refs/heads/{head_branch}")
        if not quiet:
            err("warning: You appear to have cloned an empty repository.\ndone.\n")
        return 0
    transport.transfer(src, repo, list(dict.fromkeys(refs.values())))
    local_refs, peeled = {}, {}
    for ref, oid in refs.items():
        if ref.startswith("refs/heads/"):
            name = ref if bare else f"refs/remotes/{origin}/" + ref[len("refs/heads/"):]
        elif ref.startswith("refs/tags/"):
            name = ref
        elif bare:
            name = ref
        else:
            continue
        local_refs[name] = oid
        if ref.startswith("refs/tags/"):
            target = transport.peel_to_commit(repo, oid) if repo.odb.read(oid)[0] == b"tag" else None
            if target and target != oid:
                peeled[name] = target
    lrefs = Refs(repo)
    lrefs.write_packed(local_refs, peeled)
    head_oid = refs.get("refs/heads/" + head_branch)
    if branch_opt and head_oid is None:
        raise GitError(f"Remote branch {branch_opt} not found in upstream {origin}")
    if not bare:
        if head_oid is not None:
            lrefs.set_symbolic(f"refs/remotes/{origin}/HEAD", f"refs/remotes/{origin}/{head_branch}")
            lrefs.update(f"refs/heads/{head_branch}", head_oid, f"clone: from {url}")
            configmod.set_value(cfg, f"branch.{head_branch}.remote", origin)
            configmod.set_value(cfg, f"branch.{head_branch}.merge", f"refs/heads/{head_branch}")
            repo.reload_config()
            from pygit.checkout import switch_trees
            from pygit.objects import Commit
            switch_trees(repo, None, Commit.parse(repo.odb.read(head_oid)[1]).tree, force=True)
    if not quiet:
        err("done.\n")
    return 0


# -- fetch ------------------------------------------------------------------------------

def _fetch_line(code: str, summary: str, src: str, dst: str, width: int, suffix: str = "") -> str:
    return f" {code} {summary:<{SUMMARY_WIDTH}} {src:<{width}} -> {dst}{suffix}\n"


def do_fetch(repo, remote: str, prune: bool = False, quiet: bool = False,
             explicit: list[str] | None = None) -> tuple[int, list]:
    """Fetch from a configured remote (or a path). Returns (rc, fetched
    head entries for FETCH_HEAD: [(oid, for_merge, description)])."""
    url = _remote_url(repo, remote)
    src = transport.open_repo(url)
    refspecs = repo.config.get_all(f"remote.{remote}.fetch") if not explicit else explicit
    refspecs = [r for r in refspecs if r is not None]
    remote_refs = transport.advertised_refs(src)
    refs = Refs(repo)
    updates = []   # (remote ref, local ref or None, new oid)
    if not explicit:
        for rref, oid in remote_refs.items():
            for spec in refspecs:
                local = transport.map_ref(spec, rref)
                if local is not None:
                    updates.append((rref, local, oid, transport.parse_refspec(spec)[0]))
                    break
    else:
        # "git fetch <remote> <branch>": fetch the ref into FETCH_HEAD only.
        for spec in explicit:
            force, s, d = transport.parse_refspec(spec)
            full = s if s.startswith("refs/") else f"refs/heads/{s}"
            if full not in remote_refs and f"refs/tags/{s}" in remote_refs:
                full = f"refs/tags/{s}"
            if full not in remote_refs:
                raise GitError(f"couldn't find remote ref {s}")
            updates.append((full, d or None, remote_refs[full], force))
    # Tags pointing into fetched history follow automatically.
    wanted = [oid for _, _, oid, _ in updates]
    transport.transfer(src, repo, list(dict.fromkeys(wanted)))
    if not explicit:
        for rref, oid in remote_refs.items():
            if rref.startswith("refs/tags/") and not any(u[0] == rref for u in updates):
                commit = transport.peel_to_commit(src, oid)
                if commit and repo.odb.exists(commit) and not refs.exists(rref):
                    transport.transfer(src, repo, [oid])
                    updates.append((rref, rref, oid, False))
    lines = []
    head_entries = []
    rc = 0
    cur_branch = refs.head_branch()
    merge_ref = None
    if cur_branch and cur_branch.startswith("refs/heads/"):
        b = cur_branch[len("refs/heads/"):]
        if repo.config.get(f"branch.{b}.remote") == remote:
            merge_ref = repo.config.get(f"branch.{b}.merge")
    deletions = []
    if prune and not explicit:
        mapped = {transport.map_ref(s, r) for s in refspecs for r in remote_refs}
        for spec in refspecs:
            _, s_src, s_dst = transport.parse_refspec(spec)
            prefix = s_dst.partition("*")[0]
            for lref in refs.list(prefix):
                if lref not in mapped and lref != f"refs/remotes/{remote}/HEAD":
                    deletions.append(lref)
    width = max([10] + [len(transport.shorten(r)) for r, _, _, _ in updates] +
                ([len("(none)")] if deletions else []))
    for lref in deletions:
        refs.delete(lref)
        lines.append(_fetch_line("-", "[deleted]", "(none)", transport.shorten(lref), width))
    for rref, lref, oid, force in sorted(updates, key=lambda u: u[0].encode()):
        kind = "tag" if rref.startswith("refs/tags/") else "branch" if rref.startswith("refs/heads/") else "ref"
        desc = f"{kind} '{transport.shorten(rref)}' of {url}" if kind != "ref" else f"'{rref}' of {url}"
        for_merge = (rref == merge_ref) if not explicit else True
        head_entries.append((oid, for_merge, desc))
        if lref is None:
            if explicit:
                lines.append(_fetch_line("*", "branch" if kind == "branch" else kind, transport.shorten(rref),
                                         "FETCH_HEAD", width))
            continue
        old, _ = refs.resolve(lref)
        short_src = transport.shorten(rref)
        short_dst = transport.shorten(lref)
        if old == oid:
            continue
        if old is None:
            label = "[new tag]" if kind == "tag" else "[new branch]" if kind == "branch" else "[new ref]"
            refs.update(lref, oid, f"fetch: storing head" if kind != "tag" else "")
            lines.append(_fetch_line("*", label, short_src, short_dst, width))
            continue
        if kind == "tag":
            if not force:
                lines.append(_fetch_line("!", "[rejected]", short_src, short_dst, width, "  (would clobber existing tag)"))
                rc = 1
                continue
        a, b = revparse.short_id(repo, old), revparse.short_id(repo, oid)
        if transport.is_ancestor_in(repo, old, oid):
            refs.update(lref, oid, "fetch: fast-forward")
            lines.append(_fetch_line(" ", f"{a}..{b}", short_src, short_dst, width))
        elif force:
            refs.update(lref, oid, "fetch: forced-update")
            lines.append(_fetch_line("+", f"{a}...{b}", short_src, short_dst, width, "  (forced update)"))
        else:
            lines.append(_fetch_line("!", "[rejected]", short_src, short_dst, width, "  (non-fast-forward)"))
            rc = 1
    if lines and not quiet:
        err(f"From {url}\n" + "".join(lines))
    fh = [e for e in head_entries if e[1]] + [e for e in head_entries if not e[1]]
    data = "".join(f"{oid}\t{'' if m else 'not-for-merge'}\t{d}\n" for oid, m, d in fh)
    from pygit.lockfile import write_locked
    write_locked(repo.gitdir / "FETCH_HEAD", data.encode())
    return rc, fh


@command("fetch")
def cmd_fetch(args):
    repo = find_repo()
    prune = quiet = False
    pos = []
    for arg in args:
        if arg in ("-p", "--prune"):
            prune = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            pos.append(arg)
    remote = pos[0] if pos else _default_remote(repo)
    rc, _ = do_fetch(repo, remote, prune, quiet, explicit=pos[1:] or None)
    return rc


def _default_remote(repo) -> str:
    from pygit.branching import current_branch
    b = current_branch(repo)
    if b:
        r = repo.config.get(f"branch.{b}.remote")
        if r:
            return r
    return "origin"


# -- push -------------------------------------------------------------------------------

_DENY_CURRENT = [
    "error: refusing to update checked out branch: {ref}",
    "error: By default, updating the current branch in a non-bare repository",
    "is denied, because it will make the index and work tree inconsistent",
    "with what you pushed, and will require 'git reset --hard' to match",
    "the work tree to HEAD.",
    "",
    "You can set the 'receive.denyCurrentBranch' configuration variable",
    "to 'ignore' or 'warn' in the remote repository to allow pushing into",
    "its current branch; however, this is not recommended unless you",
    "arranged to update its work tree to match what you pushed in some",
    "other way.",
    "",
    "To squelch this message and still keep the default behaviour, set",
    "'receive.denyCurrentBranch' configuration variable to 'refuse'.",
]


def _remote_say(lines: list[str]) -> str:
    return "".join(f"remote: {l}        \n" if l else "remote: \n" for l in lines)


@command("push")
def cmd_push(args):
    repo = find_repo()
    force = delete = set_upstream = tags = quiet = False
    pos = []
    for arg in args:
        if arg in ("-f", "--force"):
            force = True
        elif arg in ("-d", "--delete"):
            delete = True
        elif arg in ("-u", "--set-upstream"):
            set_upstream = True
        elif arg == "--tags":
            tags = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg.startswith("-"):
            raise GitError(f"unknown option '{arg}'")
        else:
            pos.append(arg)
    from pygit.branching import current_branch
    remote = pos[0] if pos else _default_remote(repo)
    url = _remote_url(repo, remote)
    dst_repo = transport.open_repo(url)
    specs = pos[1:]
    branch = current_branch(repo)
    if not specs and not tags:
        if branch is None:
            raise GitError("You are not currently on a branch.\n"
                           "To push the history leading to the current (detached HEAD)\n"
                           "state now, use\n\n    git push origin HEAD:<name-of-remote-branch>\n")
        up = repo.config.get(f"branch.{branch}.merge")
        if not up and not set_upstream:
            raise GitError(f"The current branch {branch} has no upstream branch.\n"
                           "To push the current branch and set the remote as upstream, use\n\n"
                           f"    git push --set-upstream {remote} {branch}\n\n"
                           "To have this happen automatically for branches without a tracking\n"
                           "upstream, see 'push.autoSetupRemote' in 'git help config'.\n")
        specs = [branch]
    plan = []   # (src label, src oid or None, dst ref, force)
    for spec in specs:
        f, s, d = transport.parse_refspec(spec)
        if delete:
            d, s = (s if s.startswith("refs/") else f"refs/heads/{s}"), ""
            plan.append(("", None, d, True))
            continue
        if s == "":
            plan.append(("", None, d if d.startswith("refs/") else f"refs/heads/{d}", True))
            continue
        try:
            oid = revparse.resolve(repo, s)
        except GitError:
            raise GitError(f"src refspec {s} does not match any")
        if not d:
            got = revparse.resolve_ref_name(repo, s)
            if got and got[1].startswith(("refs/heads/", "refs/tags/")):
                d = got[1]
            else:
                raise GitError(f"The destination you provided is not a full refname (i.e.,\n"
                               f"starting with \"refs/\"). ...")
        elif not d.startswith("refs/"):
            got = revparse.resolve_ref_name(repo, s)
            if got and got[1].startswith("refs/tags/"):
                d = "refs/tags/" + d
            else:
                d = "refs/heads/" + d
        plan.append((s, oid, d, f or force))
    if tags:
        for ref, oid in Refs(repo).list("refs/tags/").items():
            plan.append((ref[len("refs/tags/"):], oid, ref, force))
    rrefs = Refs(dst_repo)
    remote_current = rrefs.head_branch() if dst_repo.worktree is not None else None
    deny = (dst_repo.config.get("receive.denyCurrentBranch") or "refuse").lower()
    results = []   # (code, summary, src, dst, suffix)
    remote_msgs = []
    rejected_nonff = None
    ok_updates = []
    for s, oid, d, f in plan:
        old, _ = rrefs.resolve(d)
        shown_dst = transport.shorten(d)
        shown = f"{s} -> {shown_dst}" if s else shown_dst
        if oid is None:
            if old is None:
                results.append(("!", "[remote rejected]", shown, " (remote ref does not exist)"))
                continue
            if d == remote_current and deny in ("refuse", "true"):
                # receive-pack checks "checked out" before "deleting".
                remote_msgs += [l.format(ref=d) for l in _DENY_CURRENT]
                results.append(("!", "[remote rejected]", shown, " (branch is currently checked out)"))
                continue
            ok_updates.append((d, None, old))
            results.append(("-", "[deleted]", shown, ""))
            continue
        if old == oid:
            results.append(("=", "[up to date]", shown, ""))
            continue
        if old is not None and not f:
            is_tag = d.startswith("refs/tags/")
            if is_tag:
                results.append(("!", "[rejected]", shown, " (already exists)"))
                rejected_nonff = rejected_nonff or "tag"
                continue
            if not dst_repo.odb.exists(old) or not repo.odb.exists(old) or \
                    not transport.is_ancestor_in(repo, old, oid):
                fetch_first = not repo.odb.exists(old)
                results.append(("!", "[rejected]", shown, " (fetch first)" if fetch_first else " (non-fast-forward)"))
                kind = "fetch-first" if fetch_first else (
                    "current" if branch is not None and s in (branch, f"refs/heads/{branch}", "HEAD") else "other")
                rejected_nonff = rejected_nonff or kind
                continue
        if d == remote_current and deny in ("refuse", "true"):
            remote_msgs += [l.format(ref=d) for l in _DENY_CURRENT]
            results.append(("!", "[remote rejected]", shown, " (branch is currently checked out)"))
            continue
        ok_updates.append((d, oid, old))
        if old is None:
            label = "[new tag]" if d.startswith("refs/tags/") else "[new branch]" if d.startswith("refs/heads/") \
                else "[new reference]"
            results.append(("*", label, shown, ""))
        elif transport.is_ancestor_in(repo, old, oid):
            results.append((" ", f"{revparse.short_id(repo, old)}..{revparse.short_id(repo, oid)}", shown, ""))
        else:
            results.append(("+", f"{revparse.short_id(repo, old)}...{revparse.short_id(repo, oid)}", shown,
                            " (forced update)"))
    wants = [oid for _, oid, _ in ok_updates if oid]
    if wants:
        transport.transfer(repo, dst_repo, wants)
    for d, oid, old in ok_updates:
        if oid is None:
            rrefs.delete(d)
        else:
            rrefs.update(d, oid, "push")
    # Remote-tracking refs follow successful pushes.
    for d, oid, old in ok_updates:
        if d.startswith("refs/heads/") and repo.config.get(f"remote.{remote}.url"):
            track = f"refs/remotes/{remote}/{d[len('refs/heads/'):]}"
            lr = Refs(repo)
            if oid is None:
                if lr.exists(track):
                    lr.delete(track)
            else:
                lr.update(track, oid, "update by push")
    if set_upstream:
        for (s, oid, d, f), r in zip(plan, results):
            if r[0] not in ("!",) and oid and d.startswith("refs/heads/"):
                got = revparse.resolve_ref_name(repo, s)
                if got and got[1].startswith("refs/heads/"):
                    b = got[1][len("refs/heads/"):]
                    cfg = repo.gitdir / "config"
                    configmod.set_value(cfg, f"branch.{b}.remote", remote)
                    configmod.set_value(cfg, f"branch.{b}.merge", d)
                    repo.reload_config()
                    if not quiet:
                        out(f"branch '{b}' set up to track '{remote}/{d[len('refs/heads/'):]}'.\n".encode())
    # Report (git prints only refs that changed or failed).
    shown_results = [r for r in results if r[0] != "="]
    failed = any(r[0] == "!" for r in results)
    text = _remote_say(remote_msgs)
    if shown_results:
        width = SUMMARY_WIDTH
        text += f"To {url}\n" + "".join(f" {c} {summ:<{width}} {shown}{suf}\n" for c, summ, shown, suf in shown_results)
    elif not failed and not quiet:
        text += "Everything up-to-date\n"
    if failed:
        text += f"error: failed to push some refs to '{url}'\n"
        text += _push_hint(rejected_nonff)
    if not quiet or failed:
        err(text)
    return 1 if failed else 0


def _push_hint(kind) -> str:
    if kind == "current":
        return ("hint: Updates were rejected because the tip of your current branch is behind\n"
                "hint: its remote counterpart. If you want to integrate the remote changes,\n"
                "hint: use 'git pull' before pushing again.\n"
                "hint: See the 'Note about fast-forwards' in 'git push --help' for details.\n")
    if kind == "other":
        return ("hint: Updates were rejected because a pushed branch tip is behind its remote\n"
                "hint: counterpart. If you want to integrate the remote changes, use 'git pull'\n"
                "hint: before pushing again.\n"
                "hint: See the 'Note about fast-forwards' in 'git push --help' for details.\n")
    if kind == "fetch-first":
        return ("hint: Updates were rejected because the remote contains work that you do not\n"
                "hint: have locally. This is usually caused by another repository pushing to\n"
                "hint: the same ref. If you want to integrate the remote changes, use\n"
                "hint: 'git pull' before pushing again.\n"
                "hint: See the 'Note about fast-forwards' in 'git push --help' for details.\n")
    if kind == "tag":
        return "hint: Updates were rejected because the tag already exists in the remote.\n"
    return ""


# -- pull --------------------------------------------------------------------------------

@command("pull")
def cmd_pull(args):
    repo = find_repo()
    quiet = "-q" in args or "--quiet" in args
    ff_only = "--ff-only" in args
    pos = [a for a in args if not a.startswith("-")]
    remote = pos[0] if pos else _default_remote(repo)
    rc, entries = do_fetch(repo, remote, quiet=quiet, explicit=pos[1:] or None)
    if rc:
        return rc
    merge = [e for e in entries if e[1]]
    if not merge:
        raise GitError("There is no tracking information for the current branch.")
    oid, _, desc = merge[0]
    from pygit.commands.merging import cmd_merge
    from pygit.history import is_ancestor
    head, _ = Refs(repo).resolve("HEAD")
    if head and not is_ancestor(repo, head, oid) and not is_ancestor(repo, oid, head):
        if ff_only or (repo.config.get("pull.ff") or "") == "only":
            raise GitError("Not possible to fast-forward, aborting.")
        if repo.config.get("pull.rebase") is None and repo.config.get("pull.ff") is None:
            err("hint: You have divergent branches and need to specify how to reconcile them.\n"
                "hint: You can do so by running one of the following commands sometime before\n"
                "hint: your next pull:\n"
                "hint:\n"
                "hint:   git config pull.rebase false  # merge\n"
                "hint:   git config pull.rebase true   # rebase\n"
                "hint:   git config pull.ff only       # fast-forward only\n"
                "hint:\n"
                "hint: You can replace \"git config\" with \"git config --global\" to set a default\n"
                "hint: preference for all repositories. You can also pass --rebase, --no-rebase,\n"
                "hint: or --ff-only on the command line to override the configured default per\n"
                "hint: invocation.\n")
            raise GitError("Need to specify how to reconcile divergent branches.")
    return cmd_merge(["-m", f"Merge {desc}", oid])


# -- remote ------------------------------------------------------------------------------

@command("remote")
def cmd_remote(args):
    repo = find_repo()
    cfg = repo.gitdir / "config"
    names = []
    for s, sub, k, v in repo.config.entries:
        if s == "remote" and sub and sub not in names:
            names.append(sub)
    if not args or args == ["-v"] or args == ["--verbose"]:
        for n in names:
            if args:
                url = repo.config.get(f"remote.{n}.url") or ""
                out(f"{n}\t{url} (fetch)\n{n}\t{url} (push)\n".encode())
            else:
                out(f"{n}\n".encode())
        return 0
    sub = args[0]
    if sub == "add":
        if len(args) != 3:
            raise GitError("usage: git remote add <name> <url>")
        name, url = args[1], args[2]
        if name in names:
            err(f"error: remote {name} already exists.\n")
            return 3
        configmod.set_value(cfg, f"remote.{name}.url", url)
        configmod.set_value(cfg, f"remote.{name}.fetch", f"+refs/heads/*:refs/remotes/{name}/*")
        return 0
    if sub in ("remove", "rm"):
        name = args[1]
        if name not in names:
            err(f"error: No such remote: '{name}'\n")
            return 2
        from pygit.commands.branching import _remove_config_section
        _remove_config_section(repo, f'remote "{name}"')
        refs = Refs(repo)
        for ref in list(refs.list(f"refs/remotes/{name}/")):
            refs.delete(ref)
        sym = f"refs/remotes/{name}/HEAD"
        if (repo.gitdir / sym).exists():
            (repo.gitdir / sym).unlink()
        return 0
    if sub == "get-url":
        url = repo.config.get(f"remote.{args[1]}.url")
        if url is None:
            err(f"error: No such remote '{args[1]}'\n")
            return 2
        out(url.encode() + b"\n")
        return 0
    raise GitError(f"unsupported remote subcommand '{sub}'")

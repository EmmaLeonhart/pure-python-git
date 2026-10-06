"""Branch state shown by status: detached HEAD descriptions and upstreams."""

from __future__ import annotations

from pygit.history import ahead_behind
from pygit.refs import Refs


def current_branch(repo) -> str | None:
    """Short name of the checked-out branch, or None if detached."""
    target = Refs(repo).head_branch()
    if target and target.startswith("refs/heads/"):
        return target[len("refs/heads/"):]
    return target


def detached_description(repo) -> bytes:
    """"HEAD detached at X" / "HEAD detached from X", from the HEAD reflog."""
    log = repo.gitdir / "logs" / "HEAD"
    head, _ = Refs(repo).resolve("HEAD")
    try:
        lines = log.read_bytes().splitlines()
    except FileNotFoundError:
        lines = []
    for line in reversed(lines):
        meta, _, msg = line.partition(b"\t")
        if msg.startswith(b"checkout: moving from "):
            target = msg.rsplit(b" to ", 1)[-1]
            new = meta.split(b" ")[1].decode()
            from pygit import revparse
            # git shows the name the user checked out if it still names HEAD.
            short = target
            if len(target) == 40:
                short = revparse.short_id(repo, target.decode()).encode()
            if head == new:
                return b"HEAD detached at " + short
            return b"HEAD detached from " + short
    return b"Not currently on any branch."


def upstream(repo, branch: str) -> tuple[str, str] | None:
    """(display name like 'origin/main', full ref) of a branch's upstream."""
    remote = repo.config.get(f"branch.{branch}.remote")
    merge = repo.config.get(f"branch.{branch}.merge")
    if not remote or not merge:
        return None
    if remote == ".":
        full = merge
        short = merge[len("refs/heads/"):] if merge.startswith("refs/heads/") else merge
        return short, full
    name = merge[len("refs/heads/"):] if merge.startswith("refs/heads/") else merge
    return f"{remote}/{name}", f"refs/remotes/{remote}/{name}"


def _plural(n: int) -> str:
    return "commit" if n == 1 else "commits"


def tracking_info(repo) -> bytes | None:
    """The "Your branch is ..." paragraph of `git status`."""
    branch = current_branch(repo)
    if branch is None:
        return None
    up = upstream(repo, branch)
    if up is None:
        return None
    short, full = up
    refs = Refs(repo)
    local, _ = refs.resolve("refs/heads/" + branch)
    remote, _ = refs.resolve(full)
    if remote is None:
        return (f"Your branch is based on '{short}', but the upstream is gone.\n"
                f'  (use "git branch --unset-upstream" to fixup)\n').encode()
    if local is None:
        return None
    ahead, behind = ahead_behind(repo, local, remote)
    if not ahead and not behind:
        return f"Your branch is up to date with '{short}'.\n".encode()
    if ahead and not behind:
        return (f"Your branch is ahead of '{short}' by {ahead} {_plural(ahead)}.\n"
                f'  (use "git push" to publish your local commits)\n').encode()
    if behind and not ahead:
        return (f"Your branch is behind '{short}' by {behind} {_plural(behind)}, "
                f"and can be fast-forwarded.\n"
                f'  (use "git pull" to update your local branch)\n').encode()
    return (f"Your branch and '{short}' have diverged,\n"
            f"and have {ahead} and {behind} different commits each, respectively.\n"
            f'  (use "git pull" if you want to integrate the remote branch with yours)\n').encode()


def short_branch_header(repo) -> bytes:
    """The `## ...` line of `git status -sb`."""
    refs = Refs(repo)
    branch = current_branch(repo)
    if branch is None:
        return b"## HEAD (no branch)"
    local, _ = refs.resolve("refs/heads/" + branch)
    if local is None:
        return b"## No commits yet on " + branch.encode()
    up = upstream(repo, branch)
    if up is None:
        return b"## " + branch.encode()
    short, full = up
    remote, _ = refs.resolve(full)
    line = f"## {branch}...{short}"
    if remote is None:
        return (line + " [gone]").encode()
    ahead, behind = ahead_behind(repo, local, remote)
    parts = []
    if ahead:
        parts.append(f"ahead {ahead}")
    if behind:
        parts.append(f"behind {behind}")
    if parts:
        line += " [" + ", ".join(parts) + "]"
    return line.encode()

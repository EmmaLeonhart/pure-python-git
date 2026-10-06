"""Locating, creating and opening repositories."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pygit import config
from pygit.errors import GitError


class Repo:
    """A repository: its git directory and (unless bare) its work tree."""

    def __init__(self, gitdir: Path, worktree: Path | None):
        self.gitdir = Path(gitdir)
        self.worktree = Path(worktree) if worktree is not None else None
        self._config = None
        self._odb = None

    @property
    def odb(self):
        from pygit.objects import ObjectStore
        if self._odb is None:
            self._odb = ObjectStore(self.objects_dir)
        return self._odb

    def path(self, *parts: str) -> Path:
        return self.gitdir.joinpath(*parts)

    @property
    def objects_dir(self) -> Path:
        env = os.environ.get("GIT_OBJECT_DIRECTORY")
        return Path(env) if env else self.gitdir / "objects"

    @property
    def config(self) -> config.Config:
        if self._config is None:
            self._config = config.Config(config.global_config_paths() + [self.gitdir / "config"])
        return self._config

    def reload_config(self) -> None:
        self._config = None

    @property
    def bare(self) -> bool:
        return self.worktree is None


def _is_gitdir(p: Path) -> bool:
    return (p / "HEAD").is_file() and (p / "objects").is_dir() and (p / "refs").is_dir()


def _read_gitfile(p: Path) -> Path | None:
    """A `.git` file containing `gitdir: <path>` (worktrees, submodules)."""
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.startswith("gitdir:"):
        target = Path(text[len("gitdir:"):].strip())
        return target if target.is_absolute() else (p.parent / target).resolve()
    return None


def find_repo(start: Path | None = None) -> Repo:
    """Find the repository containing `start` (default: cwd), as git does."""
    env_dir = os.environ.get("GIT_DIR")
    if env_dir:
        gitdir = Path(env_dir).resolve()
        wt = os.environ.get("GIT_WORK_TREE")
        return Repo(gitdir, Path(wt).resolve() if wt else Path.cwd().resolve())
    cur = (start or Path.cwd()).resolve()
    while True:
        dotgit = cur / ".git"
        if dotgit.is_dir() and _is_gitdir(dotgit):
            repo = Repo(dotgit, cur)
            if repo.config.get_bool("core.bare", False):
                repo.worktree = None
            return repo
        if dotgit.is_file():
            target = _read_gitfile(dotgit)
            if target and _is_gitdir(target):
                return Repo(target, cur)
        if _is_gitdir(cur):
            # A bare repository, or a git dir entered directly: no work tree.
            return Repo(cur, None)
        if cur.parent == cur:
            raise GitError("not a git repository (or any of the parent directories): .git")
        cur = cur.parent


def default_branch() -> str:
    cfg = config.Config(config.global_config_paths())
    return cfg.get("init.defaultBranch") or "master"


def init(path: Path, bare: bool = False, initial_branch: str | None = None, quiet: bool = False) -> Repo:
    """Create (or reinitialize) a repository the way `git init` does."""
    path = Path(path).resolve()
    gitdir = path if bare else path / ".git"
    existed = (gitdir / "HEAD").exists()
    for d in ("objects/info", "objects/pack", "refs/heads", "refs/tags", "info", "hooks"):
        (gitdir / d).mkdir(parents=True, exist_ok=True)
    if not existed:
        branch = initial_branch or default_branch()
        (gitdir / "HEAD").write_bytes(f"ref: refs/heads/{branch}\n".encode())
        (gitdir / "description").write_bytes(
            b"Unnamed repository; edit this file 'description' to name the repository.\n")
        (gitdir / "info" / "exclude").write_bytes(
            b"# git ls-files --others --exclude-from=.git/info/exclude\n"
            b"# Lines that start with '#' are comments.\n"
            b"# For a project mostly in C, the following would be a good set of\n"
            b"# exclude patterns (uncomment them if you want to use them):\n"
            b"# *.[oa]\n# *~\n")
        lines = ["[core]", "\trepositoryformatversion = 0",
                 f"\tfilemode = {'false' if sys.platform == 'win32' else 'true'}",
                 f"\tbare = {'true' if bare else 'false'}"]
        if not bare:
            lines.append("\tlogallrefupdates = true")
        if sys.platform == "win32":
            lines += ["\tsymlinks = false", "\tignorecase = true"]
        (gitdir / "config").write_bytes(("\n".join(lines) + "\n").encode())
    if not quiet:
        what = "Reinitialized existing" if existed else "Initialized empty"
        shown = str(gitdir).replace("\\", "/")
        sys.stdout.buffer.write(f"{what} Git repository in {shown}/\n".encode())
    return Repo(gitdir, None if bare else path)

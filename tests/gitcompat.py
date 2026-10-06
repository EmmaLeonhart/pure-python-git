"""Test helpers: run the real `git` and pygit side by side in temp repos.

Both run as subprocesses with an isolated environment (temp HOME, no system
config, fixed identities and dates, autocrlf off) so their output can be
compared byte for byte.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")

FIXED_DATE = "1700000000 +0100"


def base_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "A U Thor",
        "GIT_AUTHOR_EMAIL": "author@example.com",
        "GIT_AUTHOR_DATE": FIXED_DATE,
        "GIT_COMMITTER_NAME": "C O Mitter",
        "GIT_COMMITTER_EMAIL": "committer@example.com",
        "GIT_COMMITTER_DATE": FIXED_DATE,
        "PYTHONPATH": str(ROOT),
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    })
    return env


@unittest.skipIf(GIT is None, "git is not installed")
class GitTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pygit-test-")
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / ".gitconfig").write_text(
            # protectNTFS off lets the fixtures index names Windows can't
            # create on disk (quotes, tabs); they never reach a work tree.
            "[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n\tprotectNTFS = false\n"
            "[advice]\n\tdetachedHead = false\n", encoding="utf-8")
        self.env = base_env(self.home)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()

    def tearDown(self):
        def onerror(func, path, exc):
            os.chmod(path, 0o700)
            func(path)
        shutil.rmtree(self._tmp.name, onerror=onerror)

    # -- running -------------------------------------------------------------------
    def run_cmd(self, argv, cwd=None, input=None, env=None, check=False):
        e = dict(self.env)
        if env:
            e.update(env)
        p = subprocess.run(argv, cwd=str(cwd or self.repo), input=input,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e)
        if check and p.returncode != 0:
            raise AssertionError(f"{argv} failed ({p.returncode}):\n{p.stderr.decode(errors='replace')}")
        return p

    def git(self, *args, cwd=None, input=None, env=None, check=True):
        return self.run_cmd([GIT, *args], cwd, input, env, check)

    def pygit(self, *args, cwd=None, input=None, env=None, check=True):
        return self.run_cmd([sys.executable, "-m", "pygit", *args], cwd, input, env, check)

    def git_out(self, *args, **kw) -> bytes:
        return self.git(*args, **kw).stdout

    def pygit_out(self, *args, **kw) -> bytes:
        return self.pygit(*args, **kw).stdout

    def assertSame(self, *args, cwd=None, input=None, stderr=False):
        """Both tools give the same stdout and exit code (and stderr if asked)."""
        g = self.git(*args, cwd=cwd, input=input, check=False)
        p = self.pygit(*args, cwd=cwd, input=input, check=False)
        self.assertEqual(p.stdout, g.stdout, f"stdout differs for {args}\n"
                         f"pygit stderr: {p.stderr.decode(errors='replace')}")
        self.assertEqual(p.returncode, g.returncode,
                         f"exit code differs for {args}: git {g.returncode}, pygit {p.returncode}\n"
                         f"git stderr: {g.stderr.decode(errors='replace')}\n"
                         f"pygit stderr: {p.stderr.decode(errors='replace')}")
        if stderr:
            self.assertEqual(p.stderr, g.stderr)
        return g

    # -- fixtures --------------------------------------------------------------------
    def write(self, rel: str, data, base=None) -> Path:
        p = (base or self.repo) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode()
        p.write_bytes(data)
        return p

    def git_init(self, path=None):
        self.git("init", "-q", str(path or self.repo))

    def sample_repo(self):
        """A git-made repo with nested dirs, odd names, an exec bit, a tag."""
        self.git_init()
        self.write("a.txt", "hello\n")
        self.write("sp ace.txt", "x")
        self.write("café.txt", "é")
        self.write("sub/s.txt", "s")
        self.write("sub/deep/d.txt", "d\n")
        self.write("sub-file", "dash\n")
        self.write("empty", b"")
        self.write("bin.dat", bytes(range(256)))
        self.git("add", "-A")
        self.git("update-index", "--chmod=+x", "sub/s.txt")
        # Names Windows can't create on disk go straight into the index.
        q = self.git_out("hash-object", "-w", "--stdin", input=b"q\n").strip().decode()
        self.git("update-index", "--add", "--cacheinfo", f"100644,{q},quo\"te\ttab.txt")
        self.git("update-index", "--add", "--cacheinfo", f"120000,{q},link")
        self.git("commit", "-q", "-m", "first commit")
        self.write("a.txt", "hello\nworld\n")
        self.git("commit", "-q", "-am", "second\n\nwith a body\n")
        self.git("tag", "light")
        self.git("tag", "-a", "v1", "-m", "annotated tag")
        self.git("tag", "-a", "v1-nested", "-m", "tag of a tag", "v1")

"""Stage 5: clone, fetch, push, pull, remote between local repositories.

Each scenario is played twice from identical starting points, once with git
and once with pygit as the client, against its own copy of the "server"
repository. Outputs (with the twin paths normalized), exit codes, and the
resulting refs, config, FETCH_HEAD and objects must match.
"""

import os
import shutil
import unittest

from gitcompat import GitTestCase


class TransportTwin(GitTestCase):
    def setUp(self):
        super().setUp()
        self.sides = {}
        for tool in ("git", "py"):
            root = self.tmp / tool
            root.mkdir()
            self.sides[tool] = root

    def server(self):
        """The same source repository on both sides (built once, copied)."""
        src = self.tmp / "proto"
        self.git("init", "-q", str(src))
        self.write("a", "a\n", base=src)
        self.write("dir/b", "b\n", base=src)
        self.git("add", "-A", cwd=src)
        self.git("commit", "-q", "-m", "one", cwd=src)
        self.git("branch", "side", cwd=src)
        self.git("tag", "-a", "v1", "-m", "tag", cwd=src)
        self.write("a", "a\na2\n", base=src)
        self.git("commit", "-q", "-am", "two", cwd=src)
        for root in self.sides.values():
            shutil.copytree(src, root / "src")

    def both(self, fn):
        for root in self.sides.values():
            fn(root)

    def run_both(self, *args, cwd_rel="."):
        g = self.git(*args, cwd=self.sides["git"] / cwd_rel, check=False)
        p = self.pygit(*args, cwd=self.sides["py"] / cwd_rel, check=False)

        def norm(b, root):
            return b.replace(str(root).replace("\\", "/").encode(), b"<root>")

        self.assertEqual(norm(p.stdout, self.sides["py"]), norm(g.stdout, self.sides["git"]),
                         f"stdout differs for {args}")
        self.assertEqual(norm(p.stderr, self.sides["py"]), norm(g.stderr, self.sides["git"]),
                         f"stderr differs for {args}")
        self.assertEqual(p.returncode, g.returncode, f"exit code differs for {args}")

    def state(self, rel):
        """Refs, config, FETCH_HEAD, HEAD and objects of a repository."""
        out = {}
        for tool, root in self.sides.items():
            d = root / rel
            if not d.exists():
                out[tool] = None
                continue
            gitdir = d / ".git" if (d / ".git").is_dir() else d

            def norm(b):
                return b.replace(str(root).replace("\\", "/").encode(), b"<root>")

            refs = self.git_out("for-each-ref", "--format=%(refname) %(objectname) %(symref)", cwd=d)
            cfg = norm((gitdir / "config").read_bytes())
            fh = norm((gitdir / "FETCH_HEAD").read_bytes()) if (gitdir / "FETCH_HEAD").exists() else None
            head = (gitdir / "HEAD").read_bytes()
            objs = sorted(self.git_out("cat-file", "--batch-check=%(objectname)", "--batch-all-objects",
                                       cwd=d).split())
            status = self.git_out("status", "--porcelain", cwd=d) if (d / ".git").is_dir() else None
            out[tool] = (refs, cfg, fh, head, objs, status)
        self.assertEqual(out["py"], out["git"], f"state of {rel} differs")


class TestClone(TransportTwin):
    def test_clone(self):
        self.server()
        self.run_both("clone", "src", "dst")
        self.state("dst")
        self.run_both("clone", "src")  # destination exists
        self.run_both("clone", "--bare", "src", "b.git")
        self.state("b.git")
        self.run_both("clone", "nowhere", "x")
        self.git("fsck", "--strict", cwd=self.sides["py"] / "dst")
        self.assertEqual(self.git_out("reflog", "--format=%gs", cwd=self.sides["py"] / "dst"),
                         self.git_out("reflog", "--format=%gs", cwd=self.sides["git"] / "dst")
                         .replace(str(self.sides["git"]).replace("\\", "/").encode(),
                                  str(self.sides["py"]).replace("\\", "/").encode()))

    def test_clone_empty(self):
        for root in self.sides.values():
            self.git("init", "-q", str(root / "empty"))
        self.run_both("clone", "empty", "e2")
        self.state("e2")


class TestFetchPush(TransportTwin):
    def setUp(self):
        super().setUp()
        self.server()
        self.both(lambda r: self.git("clone", "-q", "src", "dst", cwd=r))

    def in_both(self, rel, *args):
        self.both(lambda r: self.git(*args, cwd=r / rel))

    def test_fetch(self):
        self.run_both("fetch", cwd_rel="dst")
        self.in_both("src", "commit", "-q", "--allow-empty", "-m", "three")
        self.in_both("src", "branch", "new")
        self.in_both("src", "tag", "light")
        self.in_both("src", "branch", "-D", "-q", "side")
        self.run_both("fetch", cwd_rel="dst")
        self.state("dst")
        self.run_both("fetch", "--prune", cwd_rel="dst")
        self.state("dst")
        self.in_both("src", "reset", "-q", "--hard", "HEAD~1")
        self.in_both("src", "commit", "-q", "--allow-empty", "-m", "rewritten")
        self.run_both("fetch", cwd_rel="dst")
        self.state("dst")
        self.run_both("fetch", "nowhere", cwd_rel="dst")

    def test_push(self):
        self.in_both("dst", "checkout", "-q", "-b", "mine")
        self.in_both("dst", "commit", "-q", "--allow-empty", "-m", "mine")
        self.run_both("push", "origin", "mine", cwd_rel="dst")
        self.state("src")
        self.run_both("push", "origin", "mine", cwd_rel="dst")
        self.run_both("push", "origin", "mine:main", cwd_rel="dst")   # checked out on the server
        self.in_both("src", "checkout", "-q", "side")
        self.run_both("push", "origin", "mine:main", cwd_rel="dst")
        self.state("src")
        self.run_both("push", "origin", "HEAD~2:main", cwd_rel="dst")  # non-fast-forward
        self.run_both("push", "--force", "origin", "HEAD~1:main", cwd_rel="dst")
        self.run_both("push", "origin", "--delete", "mine", cwd_rel="dst")
        self.run_both("push", "origin", "--delete", "side", cwd_rel="dst")   # checked out
        self.run_both("push", "origin", "v1", cwd_rel="dst")
        self.run_both("push", cwd_rel="dst")   # no upstream
        self.run_both("push", "-u", "origin", "mine", cwd_rel="dst")
        self.state("src")
        self.state("dst")

    def test_pull_and_remote(self):
        self.run_both("remote", cwd_rel="dst")
        self.run_both("remote", "-v", cwd_rel="dst")
        self.run_both("remote", "add", "other", "../src", cwd_rel="dst")
        self.run_both("remote", "add", "other", "../src", cwd_rel="dst")
        self.run_both("remote", "get-url", "other", cwd_rel="dst")
        self.run_both("remote", "remove", "other", cwd_rel="dst")
        self.state("dst")
        self.in_both("src", "checkout", "-q", "side")
        self.in_both("src", "commit", "-q", "--allow-empty", "-m", "on side")
        self.in_both("src", "checkout", "-q", "main")
        self.in_both("src", "commit", "-q", "--allow-empty", "-m", "upstream work")
        self.run_both("pull", cwd_rel="dst")
        self.state("dst")
        self.in_both("dst", "commit", "-q", "--allow-empty", "-m", "local work")
        self.in_both("src", "commit", "-q", "--allow-empty", "-m", "more upstream work")
        self.run_both("pull", cwd_rel="dst")   # divergent without pull.rebase: refused
        self.in_both("dst", "config", "pull.rebase", "false")
        self.run_both("pull", cwd_rel="dst")
        # The merge message names the source path, which differs between the
        # twins, so compare trees, parent counts and normalized messages.
        self.run_both("log", "--format=%T %p %s", "-1", cwd_rel="dst")
        self.run_both("status", "--porcelain", cwd_rel="dst")


if __name__ == "__main__":
    unittest.main()

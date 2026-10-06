"""Stage 2: commit and log, and the diff engine they rely on.

History-changing commands run in twin repositories (one driven by git, one
by pygit) given identical setup; outputs, exit codes and resulting commit
ids must match.
"""

import os
import random
import sys
import unittest
from pathlib import Path

from gitcompat import ROOT, GitTestCase

sys.path.insert(0, str(ROOT))
from pygit import xdiff  # noqa: E402


class TwinTestCase(GitTestCase):
    def setUp(self):
        super().setUp()
        self.g = self.tmp / "g"
        self.p = self.tmp / "p"
        for d in (self.g, self.p):
            d.mkdir()
            self.git("init", "-q", cwd=d)

    def both(self, fn):
        for d in (self.g, self.p):
            fn(d)

    def write2(self, rel, data):
        self.both(lambda d: self.write(rel, data, base=d))

    def git2(self, *args):
        self.both(lambda d: self.git(*args, cwd=d))

    def step(self, *args, input=None):
        """Run `git <args>` in g and `pygit <args>` in p; compare everything."""
        g = self.git(*args, cwd=self.g, input=input, check=False)
        p = self.pygit(*args, cwd=self.p, input=input, check=False)

        def norm(b):  # the twin's path appears in some messages
            for d in (self.p, self.g):
                b = b.replace(str(d).replace("\\", "/").encode(), b"<repo>")
            return b

        self.assertEqual(norm(p.stdout), norm(g.stdout), f"stdout differs for {args}\npygit stderr: {p.stderr!r}")
        self.assertEqual(norm(p.stderr), norm(g.stderr), f"stderr differs for {args}")
        self.assertEqual(p.returncode, g.returncode, f"exit code differs for {args}")
        gh = self.git("rev-parse", "-q", "--verify", "HEAD", cwd=self.g, check=False).stdout
        ph = self.git("rev-parse", "-q", "--verify", "HEAD", cwd=self.p, check=False).stdout
        self.assertEqual(ph, gh, f"HEAD differs after {args}")
        return g


class TestCommit(TwinTestCase):
    def test_sequence(self):
        self.step("commit", "-m", "nothing yet")
        self.write2("a", "a\n")
        self.write2("b", "b\nb\n")
        self.git2("add", "a", "b")
        self.step("commit", "-m", "first")
        self.step("commit", "-m", "nothing to commit")
        self.write2("a", "a\na2\n")
        self.step("commit", "-m", "unstaged only")
        self.step("commit", "-a", "-m", "  second  \n\nwith body\t\n\n\n")
        self.git2("mv", "b", "c")
        self.write2("x", "x")
        self.git2("add", "x")
        self.git2("update-index", "--chmod=+x", "x")
        self.step("commit", "-m", "rename and new exec")
        self.git2("rm", "-q", "a")
        self.step("commit", "-m", "delete")
        self.step("commit", "--allow-empty", "-m", "empty")
        self.step("commit", "--amend", "-m", "would be empty")
        self.write2("t", "tab\there\n")
        self.git2("add", "t")
        self.step("commit", "-m", "multi", "-m", "para two", "-m", "\ttabbed line")
        self.write2("t", "changed\n")
        self.git2("add", "t")
        self.step("commit", "--amend", "-m", "amended with change")
        self.step("commit", "--amend", "--no-edit")
        self.step("commit", "-q", "--allow-empty", "-m", "quiet")
        self.step("commit", "--allow-empty", "-m", "")
        self.step("commit", "--allow-empty", "--allow-empty-message", "-m", "")

    def test_message_from_file_and_cleanup(self):
        self.write2("msg", "\n\n  subject line  \n\n\n\nbody   \n# not a comment here\n\n")
        self.write2("f", "f\n")
        self.git2("add", "f")
        self.step("commit", "-F", "msg")
        self.step("commit", "--allow-empty", "-F", "-", input=b"from stdin\n")
        self.step("commit", "--allow-empty", "--cleanup=strip", "-m", "keep\n# drop me\nkeep too")
        self.step("commit", "--allow-empty", "--cleanup=verbatim", "-m", "  verbatim  \n\n\n")

    def test_summary_shapes(self):
        lines = "".join(f"line {i}\n" for i in range(30))
        self.write2("dir/sub/file.txt", lines)
        self.write2("bin", bytes(range(256)))
        self.write2("other", "o\n")
        self.git2("add", "-A")
        self.step("commit", "-m", "root with binary")
        self.git2("mv", "dir/sub/file.txt", "dir/sub/renamed.txt")
        self.write2("dir/sub/renamed.txt", lines + "extra\n")
        self.write2("bin", bytes(range(255, -1, -1)))
        self.git2("add", "-A")
        self.step("commit", "-m", "similar rename and binary change")
        self.git2("mv", "dir/sub/renamed.txt", "elsewhere.txt")
        self.git2("rm", "-q", "other")
        self.step("commit", "-m", "rename across dirs, delete")
        self.write2("big", "".join(f"{i}\n" for i in range(200)))
        self.git2("add", "big")
        self.step("commit", "-m", "big file")
        self.write2("big", "".join(f"{i * 3 % 7}\n" for i in range(150)))
        self.git2("add", "big")
        self.step("commit", "-m", "big edit")

    def test_author_override_and_identity_line(self):
        self.write2("f", "f\n")
        self.git2("add", "f")
        self.step("commit", "-m", "x", "--author", "Someone Else <else@example.com>")
        self.step("commit", "--allow-empty", "-m", "dated", "--date", "1600000000 -0700")

    def test_detached_head(self):
        self.write2("f", "f\n")
        self.git2("add", "f")
        self.step("commit", "-m", "one")
        self.git2("checkout", "-q", "--detach")
        self.step("commit", "--allow-empty", "-m", "detached")

    def test_reflog_written(self):
        self.write2("f", "f\n")
        self.git2("add", "f")
        self.step("commit", "-m", "one")
        self.step("commit", "--allow-empty", "-m", "two")
        self.step("commit", "--amend", "-m", "two amended")
        for ref in ("HEAD", "main"):
            g = self.git_out("reflog", "show", "--format=%gs", ref, cwd=self.g)
            p = self.git_out("reflog", "show", "--format=%gs", ref, cwd=self.p)
            self.assertEqual(p, g)
        self.git("fsck", "--strict", cwd=self.p)


class TestInterop(GitTestCase):
    def test_alternating_tools(self):
        """Each tool commits on top of the other's index and history."""
        self.git_init()
        self.write("a", "a\n")
        self.write("d/b", "b\n")
        self.pygit("add", ".")
        self.pygit("commit", "-q", "-m", "pygit root")
        self.assertEqual(self.git_out("status", "--porcelain"), b"")
        self.git("fsck", "--strict")
        self.write("a", "a2\n")
        self.git("add", "a")
        self.pygit("commit", "-q", "-m", "pygit commits git's index")
        self.write("c", "c\n")
        self.pygit("add", "c")
        self.git("commit", "-q", "-m", "git commits pygit's index")
        self.assertEqual(self.git_out("status", "--porcelain"), b"")
        self.assertEqual(self.pygit_out("status", "--porcelain"), b"")
        self.git("fsck", "--strict")
        self.assertEqual(self.git_out("log", "--format=%s"),
                         b"git commits pygit's index\npygit commits git's index\npygit root\n")
        self.assertSame("log", "--oneline")


class TestLog(GitTestCase):
    def setUp(self):
        super().setUp()
        self.git_init()
        dates = iter(range(1700000000, 1800000000, 3600))

        def commit(msg, **kw):
            d = f"{next(dates)} +0530"
            self.git("commit", "-q", "--allow-empty", "-m", msg,
                     env={"GIT_AUTHOR_DATE": d, "GIT_COMMITTER_DATE": d})

        self.write("f", "1\n")
        self.git("add", "f")
        commit("first\n\nbody line\n\n\tindented\twith tabs\n\nlast paragraph")
        self.write("f", "2\n")
        self.git("add", "f")
        commit("  leading spaces subject\nsecond subject line")
        self.git("checkout", "-q", "-b", "side")
        self.write("g", "g\n")
        self.git("add", "g")
        commit("side work")
        self.git("checkout", "-q", "main")
        commit("main work")
        d = "1750000000 -0800"
        self.git("merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side",
                 env={"GIT_AUTHOR_DATE": d, "GIT_COMMITTER_DATE": d})
        commit("after merge")

    def test_formats(self):
        for args in (["log"], ["log", "--oneline"], ["log", "--pretty=short"], ["log", "--pretty=full"],
                     ["log", "--pretty=fuller"], ["log", "--pretty=raw"], ["log", "--pretty=oneline"],
                     ["log", "--abbrev-commit"], ["log", "--format=%H%n%h%n%T%n%t%n%P%n%p"],
                     ["log", "--format=%an|%ae|%ad|%at|%ai|%aI|%as|%aD"],
                     ["log", "--format=%cn|%ce|%cd|%ct|%ci|%cI|%cs"],
                     ["log", "--format=[%s]%n[%b]%n[%B]%%%x41"],
                     ["log", "--pretty=format:%h %s"], ["log", "--pretty=tformat:%h %s"],
                     ["log", "--format=%f"]):
            with self.subTest(args=args):
                self.assertSame(*args)

    def test_limits_and_ranges(self):
        for args in (["log", "-n", "2"], ["log", "-3"], ["log", "--max-count=1"], ["log", "--reverse"],
                     ["log", "--reverse", "-n", "2"], ["log", "--first-parent"], ["log", "main~2..main"],
                     ["log", "side..main"], ["log", "main..side"], ["log", "^side", "main"],
                     ["log", "side...main"], ["log", "side"], ["log", "--", "g"], ["log", "--oneline", "--", "f"],
                     ["log", "nope"]):
            with self.subTest(args=args):
                self.assertSame(*args)

    def test_diffs_in_log_and_show(self):
        self.git("tag", "-a", "v1", "-m", "tag message", "HEAD~2")
        for args in (["log", "-p"], ["log", "--oneline", "-p"], ["log", "--stat"], ["log", "--oneline", "--stat"],
                     ["log", "--format=%s", "-p"], ["log", "--pretty=format:%s", "--stat"],
                     ["log", "--name-status"], ["log", "--name-only"], ["log", "--numstat"], ["log", "--shortstat"],
                     ["log", "-p", "--stat"], ["log", "--format=%s", "-p", "--stat"], ["log", "--summary"],
                     ["show"], ["show", "--stat"], ["show", "--oneline"], ["show", "--oneline", "--stat"],
                     ["show", "--name-status"], ["show", "-p", "--stat"], ["show", "-s"], ["show", "HEAD~2"],
                     ["show", "--stat", "HEAD~2"], ["show", "v1"], ["show", "HEAD^{tree}"], ["show", "HEAD:f"],
                     ["show", "HEAD~1", "HEAD~2"], ["show", "nope"]):
            with self.subTest(args=args):
                self.assertSame(*args)

    def test_unborn(self):
        empty = self.tmp / "empty"
        self.git("init", "-q", str(empty))
        self.assertSame("log", cwd=empty)


class TestXdiff(GitTestCase):
    """The diff engine against `git diff --no-index` on random inputs."""

    VOCAB = [b"", b"{", b"}", b"    x = 1;", b"\tif (a) {", b"\t}", b"return 0;", b"int f(void)",
             b"foo", b"bar", b"baz", b"  indented", b"        deep", b"# comment", b"same", b"same"]

    def _check(self, rng, n, size, edits, span):
        fa, fb = self.tmp / "a", self.tmp / "b"
        for _ in range(n):
            base = [rng.choice(self.VOCAB) for _ in range(rng.randint(0, size))]
            new = list(base)
            for _ in range(rng.randint(0, edits)):
                pos = rng.randint(0, len(new))
                op = rng.random()
                if op < 0.4:
                    new[pos:pos] = [rng.choice(self.VOCAB) for _ in range(rng.randint(1, span))]
                elif op < 0.8 and new:
                    del new[pos:pos + rng.randint(1, span)]
                elif new:
                    new[min(pos, len(new) - 1)] = rng.choice(self.VOCAB)
            a = b"\n".join(base) + (b"\n" if base and rng.random() < 0.85 else b"")
            b = b"\n".join(new) + (b"\n" if new and rng.random() < 0.85 else b"")
            fa.write_bytes(a)
            fb.write_bytes(b)
            out = self.git("diff", "--no-index", "--no-color", str(fa), str(fb), check=False).stdout
            body = out[out.find(b"\n@@") + 1:] if b"\n@@" in out else b""
            la, lb = xdiff.split_lines(a), xdiff.split_lines(b)
            self.assertEqual(xdiff.unified(la, lb, xdiff.diff(la, lb)), body, f"a={a!r}\nb={b!r}")

    def test_small_random(self):
        self._check(random.Random(1), 150, 40, 6, 5)

    def test_large_random(self):
        """Large edit distances exercise the cost-limit heuristics."""
        self._check(random.Random(7), 6, 2400, 300, 30)


if __name__ == "__main__":
    unittest.main()

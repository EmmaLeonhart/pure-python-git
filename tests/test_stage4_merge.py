"""Stage 4: merge-base, merge-file, merge, merge --abort (compared with git)."""

import os
import random
import sys
import unittest

from gitcompat import ROOT, GitTestCase
from test_stage3_branches import Stage3Twin

sys.path.insert(0, str(ROOT))
from pygit import xmerge  # noqa: E402


class TestMergeBase(GitTestCase):
    def test_criss_cross(self):
        """Two best common ancestors."""
        self.git_init()
        dates = iter(range(1700000000, 1800000000, 60))

        def commit(msg):
            d = f"{next(dates)} +0000"
            self.git("commit", "-q", "--allow-empty", "-m", msg,
                     env={"GIT_AUTHOR_DATE": d, "GIT_COMMITTER_DATE": d})

        commit("root")
        self.git("branch", "b")
        commit("a1")
        self.git("checkout", "-q", "b")
        commit("b1")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-edit", "b", env={"GIT_AUTHOR_DATE": "1750000000 +0000",
                                                         "GIT_COMMITTER_DATE": "1750000000 +0000"})
        self.git("checkout", "-q", "b")
        self.git("merge", "-q", "--no-edit", "main~1", env={"GIT_AUTHOR_DATE": "1750000060 +0000",
                                                              "GIT_COMMITTER_DATE": "1750000060 +0000"})
        for args in (["merge-base", "main", "b"], ["merge-base", "--all", "main", "b"],
                     ["merge-base", "--is-ancestor", "main~1", "main"],
                     ["merge-base", "--is-ancestor", "main", "main~1"],
                     ["merge-base", "main~1", "b~1"], ["merge-base", "nope", "main"]):
            with self.subTest(args=args):
                self.assertSame(*args)


class TestMergeFile(GitTestCase):
    VOCAB = [b"", b"{", b"}", b"    x = 1;", b"\tif (a) {", b"\t}", b"return 0;", b"int f(void)",
             b"foo", b"bar", b"baz", b"  indented", b"same", b"same", b"--", b"//"]

    def _mutate(self, rng, lines):
        out = list(lines)
        for _ in range(rng.randint(0, 5)):
            pos = rng.randint(0, len(out))
            op = rng.random()
            if op < 0.4:
                out[pos:pos] = [rng.choice(self.VOCAB) for _ in range(rng.randint(1, 3))]
            elif op < 0.8 and out:
                del out[pos:pos + rng.randint(1, 3)]
            elif out:
                out[min(pos, len(out) - 1)] = rng.choice(self.VOCAB)
        return out

    def test_random_against_git(self):
        rng = random.Random(11)
        names = ["cur", "base", "other"]
        for i in range(120):
            base = [rng.choice(self.VOCAB) for _ in range(rng.randint(0, 25))]
            cur, other = self._mutate(rng, base), self._mutate(rng, base)
            if rng.random() < 0.2:
                other = list(cur)
            for name, lines in zip(names, (cur, base, other)):
                data = b"\n".join(lines) + (b"\n" if lines and rng.random() < 0.9 else b"")
                self.write(name, data, base=self.tmp)
            for style in ([], ["--diff3"]):
                with self.subTest(i=i, style=style):
                    self.assertSame("merge-file", "-p", *style, "-L", "ours", "-L", "base", "-L", "theirs",
                                    *names, cwd=self.tmp)

    def test_in_place_and_favor(self):
        self.write("cur", "a\nours\nc\n", base=self.tmp)
        self.write("base", "a\nb\nc\n", base=self.tmp)
        self.write("other", "a\ntheirs\nc\n", base=self.tmp)
        for opt in ("--ours", "--theirs", "--union"):
            with self.subTest(opt=opt):
                self.assertSame("merge-file", "-p", opt, "cur", "base", "other", cwd=self.tmp)
        g = self.git("merge-file", "cur", "base", "other", cwd=self.tmp, check=False)
        merged_g = (self.tmp / "cur").read_bytes()
        self.write("cur", "a\nours\nc\n", base=self.tmp)
        p = self.pygit("merge-file", "cur", "base", "other", cwd=self.tmp, check=False)
        self.assertEqual((self.tmp / "cur").read_bytes(), merged_g)
        self.assertEqual(p.returncode, g.returncode)


class TestMerge(Stage3Twin):
    def setup_branches(self):
        self.write2("f", "1\n2\n3\n4\n5\n6\n7\n8\n9\n")
        self.write2("keep", "keep\n")
        self.write2("del", "del\n")
        self.write2("moved", "".join(f"moved line {i}\n" for i in range(20)))
        self.git2("add", "-A")
        self.git2("commit", "-q", "-m", "base")
        self.git2("checkout", "-q", "-b", "side")

    def test_conflicts_and_abort(self):
        self.setup_branches()
        self.write2("f", "1\nside\n3\n4\n5\n6\n7\n8\nside end\n")
        self.write2("del", "del changed\n")
        self.write2("new", "new\n")
        self.git2("add", "-A")
        self.git2("commit", "-q", "-m", "side")
        self.git2("checkout", "-q", "main")
        self.write2("f", "1\nmain\n3\n4\n5\n6\n7\n8\n9\n")
        self.git2("rm", "-q", "del")
        self.git2("commit", "-q", "-am", "main")
        self.step("merge", "side")
        for args in (["status"], ["status", "-s"], ["ls-files", "-s"], ["diff", "--cached", "--name-status"]):
            self.step(*args)
        self.step("merge", "side")
        self.step("merge", "--abort")
        self.step("merge", "--abort")
        self.step("merge", "side")
        self.write2("f", "resolved\n")
        self.git2("add", "f")
        self.step("status")
        self.git2("rm", "-q", "del")
        self.step("status")
        self.step("commit", "--no-edit")  # without it git opens an editor
        self.step("log", "--format=%s%n%P")

    def test_fast_forward_and_up_to_date(self):
        self.setup_branches()
        self.write2("f", "changed\n")
        self.git2("rm", "-q", "del")
        self.write2("added", "a\n")
        self.git2("add", "-A")
        self.git2("commit", "-q", "-m", "side work")
        self.git2("checkout", "-q", "main")
        self.step("merge", "main")
        self.step("merge", "side")
        self.step("merge", "side")
        self.step("merge", "--ff-only", "side")
        self.step("reset", "--hard", "HEAD~1")
        self.step("merge", "--no-ff", "side")
        self.step("log", "--format=%s %P")

    def test_clean_merge_and_renames(self):
        self.setup_branches()
        self.git2("mv", "moved", "renamed")
        self.write2("f", "1\n2\n3\n4\n5\n6\n7\n8\nside\n")
        self.git2("add", "-A")
        self.git2("commit", "-q", "-m", "side renames")
        self.git2("checkout", "-q", "main")
        self.write2("moved", "".join(f"moved line {i}\n" for i in range(20)).replace("line 3", "LINE 3"))
        self.write2("f", "main\n2\n3\n4\n5\n6\n7\n8\n9\n")
        self.write2("other", "o\n")
        self.git2("add", "-A")
        self.git2("commit", "-q", "-m", "main edits")
        self.step("merge", "side")
        self.step("log", "--format=%s %P", "-1")
        self.step("cat-file", "-p", "HEAD:renamed")

    def test_add_add_and_into_branch(self):
        self.setup_branches()
        self.write2("both", "side version\n")
        self.git2("add", "both")
        self.git2("commit", "-q", "-m", "side adds")
        self.git2("checkout", "-q", "-b", "feature", "main")
        self.write2("both", "feature version\n")
        self.git2("add", "both")
        self.git2("commit", "-q", "-m", "feature adds")
        self.step("merge", "side")
        self.step("status")
        g = (self.g / ".git" / "MERGE_MSG").read_bytes()
        p = (self.p / ".git" / "MERGE_MSG").read_bytes()
        self.assertEqual(p, g)

    def test_refusals(self):
        self.setup_branches()
        self.write2("f", "side\n")
        self.git2("commit", "-q", "-am", "side")
        self.git2("checkout", "-q", "main")
        self.write2("keep", "staged\n")
        self.git2("add", "keep")
        self.step("merge", "side")
        self.git2("reset", "-q", "--hard")
        self.git2("commit", "-q", "--allow-empty", "-m", "diverge")
        self.write2("f", "dirty work tree\n")
        self.step("merge", "side")
        self.git2("checkout", "-q", "--", "f")
        self.step("merge", "--ff-only", "side")
        self.step("merge", "nope")

    def test_diff3_style(self):
        self.setup_branches()
        self.write2("f", "1\nside\n3\n4\n5\n6\n7\n8\n9\n")
        self.git2("commit", "-q", "-am", "side")
        self.git2("checkout", "-q", "main")
        self.write2("f", "1\nmain\n3\n4\n5\n6\n7\n8\n9\n")
        self.git2("commit", "-q", "-am", "main")
        self.git2("config", "merge.conflictStyle", "diff3")
        self.step("merge", "side")


if __name__ == "__main__":
    unittest.main()

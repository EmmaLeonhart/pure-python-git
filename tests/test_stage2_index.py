"""Stage 2: the index, ignore rules, add, rm, status (compared with git)."""

import os
import unittest

from gitcompat import GitTestCase


class IndexBase(GitTestCase):
    def fixture(self):
        """Untracked tree with ignore rules; nothing committed yet."""
        self.git_init()
        self.write("a", "a\n")
        self.write("b", "b\n")
        self.write("d/x", "x")
        self.write("d/e/y", "y")
        self.write("d/e/z", "z")
        self.write(".gitignore", "*.log\n/ign/\n!keep.log\nd/**/z\n")
        self.write("x.log", "l")
        self.write("keep.log", "k")
        self.write("ign/i", "i")

    def same_states(self, *variants, cwd=None):
        for args in variants:
            with self.subTest(args=args):
                self.assertSame(*args, cwd=cwd, stderr=True)


class TestIndexFormat(IndexBase):
    def test_pygit_index_is_read_by_git(self):
        self.fixture()
        self.pygit("add", "a", "d", "keep.log")
        self.assertEqual(self.git_out("ls-files", "-s"),
                         self.pygit_out("ls-files", "-s"))
        self.git("fsck")
        # git sees the stat data as fresh: nothing modified.
        self.assertEqual(self.git_out("diff-files", "--name-only"), b"")

    def test_git_index_round_trip(self):
        """pygit rewrites a git index (with extensions); git reads it back."""
        self.fixture()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")  # leaves a TREE extension
        before = self.git_out("ls-files", "-s")
        self.write("a", "changed\n")
        self.pygit("add", "a")  # forces a rewrite of the whole index
        self.write("a", "a\n")
        self.pygit("add", "a")
        self.assertEqual(self.git_out("ls-files", "-s"), before)
        self.assertSame("ls-files", "-s")
        self.assertSame("write-tree")
        self.git("fsck")

    def test_binary_bytes_in_stat_data(self):
        """Index entries are written in binary mode (0x0a must stay 0x0a)."""
        self.fixture()
        for n in range(20):
            self.write(f"f{n}", f"{n}\n" * n)
        self.pygit("add", "-A")
        self.assertSame("ls-files", "-s")
        self.git("status")

    def test_write_tree_matches_git(self):
        self.fixture()
        self.git("add", "-A")
        self.assertSame("write-tree")
        self.git("update-index", "--chmod=+x", "a")
        self.assertSame("write-tree")

    def test_read_tree(self):
        self.fixture()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        self.git("rm", "-q", "--cached", "a")
        self.pygit("read-tree", "HEAD")
        self.assertEqual(self.git_out("diff-index", "--cached", "--name-only", "HEAD"), b"")
        self.pygit("read-tree", "--empty")
        self.assertEqual(self.git_out("ls-files"), b"")


class TestLsFiles(IndexBase):
    def test_variants(self):
        self.fixture()
        self.git("add", "a", "d")
        self.same_states(["ls-files"], ["ls-files", "-s"], ["ls-files", "-z"],
                         ["ls-files", "-o"], ["ls-files", "-o", "--exclude-standard"],
                         ["ls-files", "-o", "--exclude-standard", "--directory"],
                         ["ls-files", "-o", "-i", "--exclude-standard"],
                         ["ls-files", "d"], ["ls-files", "-s", "d/e"])
        self.same_states(["ls-files"], ["ls-files", "--full-name"], ["ls-files", ".."],
                         cwd=self.repo / "d")
        os.remove(self.repo / "a")
        self.write("d/x", "changed")
        self.same_states(["ls-files", "-d"], ["ls-files", "-m"], ["ls-files", "-m", "-d"])


class TestCheckIgnore(IndexBase):
    PATTERNS = (
        "*.o\n"
        "!keep.o\n"
        "/root-only\n"
        "build/\n"
        "doc/**/*.tmp\n"
        "**/cache\n"
        "a/**/b\n"
        "x?z\n"
        "[abc]set\n"
        "[!q]neg\n"
        "\\#hash\n"
        "\\!bang\n"
        "trail\\ \n"
        "spaces   \n"
        "sub/deep\n"
        "*.[Cc][Ss][Vv]\n"
        "[[:digit:]]num\n"
        "lit[\n"
    )
    PATHS = ["x.o", "keep.o", "dir/y.o", "root-only", "dir/root-only", "build", "build/f",
             "dir/build/f", "doc/a.tmp", "doc/x/y/a.tmp", "other/doc/a.tmp", "cache", "p/q/cache",
             "a/b", "a/x/b", "a/x/y/b", "xyz", "x/z", "aset", "dset", "pneg", "qneg", "#hash",
             "!bang", "trail ", "trail", "spaces", "sub/deep", "x/sub/deep", "t.csv", "t.CsV",
             "1num", "anum", "lit[", "plain"]

    def test_patterns_match_git(self):
        self.git_init()
        self.write(".gitignore", self.PATTERNS)
        self.write("nested/.gitignore", "*.txt\n!keep.txt\n/anchored\n")
        self.write(".git/info/exclude", "excluded-by-info\n")
        for d in ("build", "dir/build", "cache", "p/q/cache"):
            (self.repo / d).mkdir(parents=True, exist_ok=True)
        paths = self.PATHS + ["nested/a.txt", "nested/keep.txt", "nested/anchored",
                              "nested/x/anchored", "nested/x/a.txt", "excluded-by-info"]
        for p in paths:
            with self.subTest(path=p):
                self.assertSame("check-ignore", "-v", "--no-index", p)
        self.assertSame("check-ignore", *paths)
        self.assertSame("check-ignore", "-v", "-n", *paths)

    def test_excluded_parent_wins(self):
        self.git_init()
        self.write(".gitignore", "out/\n!out/keep\n")
        self.write("out/keep", "k")
        self.assertSame("check-ignore", "-v", "out/keep")

    def test_tracked_files_are_not_ignored(self):
        self.git_init()
        self.write("t.log", "x")
        self.git("add", "t.log")
        self.write(".gitignore", "*.log\n")
        self.assertSame("check-ignore", "t.log")
        self.assertSame("check-ignore", "--no-index", "t.log")


class TestAddRm(IndexBase):
    def test_add_variants(self):
        for args in (["add", "."], ["add", "-A"], ["add", "d"], ["add", "d/e"], ["add", "*.log"],
                     ["add", "a", "keep.log"], ["add", "-f", "x.log"], ["add", "-n", "."],
                     ["add", "ign/i"], ["add", "x.log"], ["add", "nope"], ["add"]):
            with self.subTest(args=args):
                self.tearDown()
                self.setUp()
                self.fixture()
                g = self.git(*args, check=False)
                gi = self.git_out("ls-files", "-s")
                self.git("read-tree", "--empty")
                p = self.pygit(*args, check=False)
                self.assertEqual(p.stdout, g.stdout)
                self.assertEqual(p.stderr, g.stderr)
                self.assertEqual(p.returncode, g.returncode)
                self.assertEqual(self.git_out("ls-files", "-s"), gi)

    def test_add_update_and_removal(self):
        self.fixture()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        self.write("a", "changed\n")
        os.remove(self.repo / "b")
        self.write("new", "n")
        for args in (["add", "-u"], ["add", "-A"], ["add", "--ignore-removal", "."], ["add", "."]):
            with self.subTest(args=args):
                self.git("reset", "-q")
                self.git(*args)
                expect = self.git_out("ls-files", "-s")
                self.git("reset", "-q")
                self.pygit(*args)
                self.assertEqual(self.git_out("ls-files", "-s"), expect)

    def test_add_from_subdirectory(self):
        self.fixture()
        self.git("add", "e", cwd=self.repo / "d")
        expect = self.git_out("ls-files", "-s")
        self.git("read-tree", "--empty")
        self.pygit("add", "e", cwd=self.repo / "d")
        self.assertEqual(self.git_out("ls-files", "-s"), expect)

    def test_rm(self):
        def setup():
            self.tearDown()
            self.setUp()
            self.fixture()
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "c")

        cases = [["rm", "a"], ["rm", "--cached", "a"], ["rm", "d"], ["rm", "-r", "d"],
                 ["rm", "-r", "-q", "d"], ["rm", "-n", "-r", "."], ["rm", "nope"],
                 ["rm", "--ignore-unmatch", "nope"]]
        for args in cases:
            with self.subTest(args=args):
                setup()
                g = self.git(*args, check=False)
                expect = (self.git_out("ls-files", "-s"), sorted(os.listdir(self.repo)))
                setup()
                p = self.pygit(*args, check=False)
                self.assertEqual((p.stdout, p.stderr, p.returncode), (g.stdout, g.stderr, g.returncode))
                self.assertEqual((self.git_out("ls-files", "-s"), sorted(os.listdir(self.repo))), expect)

    def test_rm_safety_checks(self):
        def setup():
            self.tearDown()
            self.setUp()
            self.fixture()
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "c")

        for mutate, args in (
                (lambda: self.write("a", "local\n"), ["rm", "a"]),
                (lambda: (self.write("a", "staged\n"), self.git("add", "a")), ["rm", "a"]),
                (lambda: (self.write("a", "staged\n"), self.git("add", "a"), self.write("a", "again\n")),
                 ["rm", "a"]),
                (lambda: (self.write("a", "staged\n"), self.git("add", "a"), self.write("a", "again\n")),
                 ["rm", "--cached", "a"]),
                (lambda: (self.write("a", "1\n"), self.write("b", "2\n")), ["rm", "a", "b"]),
                (lambda: self.write("a", "local\n"), ["rm", "-f", "a"])):
            with self.subTest(args=args):
                setup()
                mutate()
                g = self.git(*args, check=False)
                setup()
                mutate()
                p = self.pygit(*args, check=False)
                self.assertEqual((p.stdout, p.stderr, p.returncode), (g.stdout, g.stderr, g.returncode))


class TestStatus(IndexBase):
    FORMATS = (["status"], ["status", "-s"], ["status", "--porcelain"], ["status", "-sb"],
               ["status", "--porcelain", "-z"], ["status", "-uall", "-s"], ["status", "-uno"],
               ["status", "--ignored"], ["status", "--ignored", "-s"], ["status", "-s", "d"])

    def check_all(self):
        self.same_states(*self.FORMATS)
        self.same_states(["status"], ["status", "-s"], ["status", "--porcelain"], cwd=self.repo / "d")

    def test_unborn(self):
        self.fixture()
        self.check_all()
        self.git("add", "a", "d")
        self.check_all()

    def test_empty_repo(self):
        self.git_init()
        self.check_all_root()

    def check_all_root(self):
        self.same_states(*[f for f in self.FORMATS if "d" not in f])

    def test_after_commit(self):
        self.fixture()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        self.check_all()
        self.write("a", "a2\n")
        os.remove(self.repo / "b")
        self.git("mv", "d/x", "d/x2")
        self.write("n", "new")
        self.git("add", "n")
        self.write("d/new-untracked", "u")
        self.check_all()
        self.git("add", "-A")
        self.check_all()

    def test_renames(self):
        self.git_init()
        body = "".join(f"line {i}\n" for i in range(50))
        self.write("orig.txt", body)
        self.write("same.txt", "identical\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        self.git("mv", "orig.txt", "moved.txt")
        (self.repo / "dir").mkdir()
        self.git("mv", "same.txt", "dir/same.txt")
        self.check_all_root()
        self.write("moved.txt", body + "one more line\n")
        self.git("add", "moved.txt")
        self.check_all_root()
        self.assertSame("status", "--no-renames", "-s")

    def test_mode_and_type_changes(self):
        self.git_init()
        self.write("f", "f\n")
        self.git("add", "f")
        self.git("commit", "-q", "-m", "c")
        self.git("update-index", "--chmod=+x", "f")
        self.check_all_root()

    def test_upstream_tracking(self):
        self.fixture()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        self.git("branch", "up")
        self.git("branch", "--set-upstream-to=up")
        self.check_all_root()
        self.git("commit", "-q", "--allow-empty", "-m", "ahead")
        self.check_all_root()
        self.git("checkout", "-q", "up")
        self.git("commit", "-q", "--allow-empty", "-m", "up moves")
        self.git("checkout", "-q", "main")
        self.check_all_root()
        self.git("branch", "-D", "-q", "up")
        self.check_all_root()


if __name__ == "__main__":
    unittest.main()

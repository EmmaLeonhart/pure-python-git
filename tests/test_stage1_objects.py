"""Stage 1: the object store, compared against the real git."""

import os
import unittest

from gitcompat import GitTestCase


class TestHashObject(GitTestCase):
    def test_hash_files_without_repo(self):
        self.write("a", "hello\n")
        self.write("b", b"")
        self.write("c", bytes(range(256)) * 10)
        self.assertSame("hash-object", "a", "b", "c")

    def test_hash_stdin_and_types(self):
        self.git_init()
        self.assertSame("hash-object", "--stdin", input=b"some data\n")
        self.assertSame("hash-object", "-t", "blob", "--stdin", input=b"")

    def test_write_is_readable_by_git(self):
        self.git_init()
        self.write("f.txt", "content written by pygit\n")
        oid = self.pygit_out("hash-object", "-w", "f.txt").strip().decode()
        self.assertEqual(self.git_out("cat-file", "-t", oid), b"blob\n")
        self.assertEqual(self.git_out("cat-file", "blob", oid), b"content written by pygit\n")
        self.git("fsck", "--strict")

    def test_written_object_file_layout(self):
        self.git_init()
        oid = self.pygit_out("hash-object", "-w", "--stdin", input=b"x").strip().decode()
        self.assertTrue((self.repo / ".git" / "objects" / oid[:2] / oid[2:]).is_file())


class TestCatFile(GitTestCase):
    def setUp(self):
        super().setUp()
        self.sample_repo()

    def test_pretty_print_every_type(self):
        for spec in ("HEAD", "HEAD^{tree}", "HEAD:a.txt", "HEAD:sub", "v1", "v1-nested",
                     "HEAD:bin.dat", "HEAD:empty"):
            with self.subTest(spec=spec):
                self.assertSame("cat-file", "-p", spec)

    def test_type_and_size(self):
        for spec in ("HEAD", "HEAD^{tree}", "HEAD:a.txt", "v1", "light", "HEAD:empty"):
            with self.subTest(spec=spec):
                self.assertSame("cat-file", "-t", spec)
                self.assertSame("cat-file", "-s", spec)

    def test_typed_and_peeling(self):
        self.assertSame("cat-file", "commit", "HEAD")
        self.assertSame("cat-file", "tree", "HEAD")
        self.assertSame("cat-file", "commit", "v1")
        self.assertSame("cat-file", "tree", "v1-nested")
        self.assertSame("cat-file", "tag", "v1-nested")
        self.assertSame("cat-file", "blob", "HEAD:sub/deep/d.txt")

    def test_exists(self):
        self.assertSame("cat-file", "-e", "HEAD")
        self.assertSame("cat-file", "-e", "0" * 39 + "1")
        self.assertSame("cat-file", "-e", "HEAD:nope")
        self.assertSame("cat-file", "-e", "no-such-ref")

    def test_errors(self):
        self.assertSame("cat-file", "-p", "no-such-ref")
        self.assertSame("cat-file", "-t", "HEAD:missing")


class TestLsTree(GitTestCase):
    def setUp(self):
        super().setUp()
        self.sample_repo()

    def test_flag_combinations(self):
        for flags in ([], ["-r"], ["-t"], ["-r", "-t"], ["-d"], ["-r", "-d"], ["-l"], ["-r", "-l"],
                      ["--name-only"], ["-r", "--name-only"], ["-z"], ["-r", "-z"],
                      ["--abbrev"], ["--object-only", "-r"]):
            with self.subTest(flags=flags):
                self.assertSame("ls-tree", *flags, "HEAD")

    def test_tree_ish_forms(self):
        for spec in ("HEAD^{tree}", "v1", "v1-nested", "HEAD~1", "HEAD:sub", "main"):
            with self.subTest(spec=spec):
                self.assertSame("ls-tree", "-r", spec)

    def test_pathspecs(self):
        cases = [["sub"], ["sub/"], ["sub/deep"], ["sub/deep/"], ["sub/deep/d.txt"], ["a.txt", "sub/s.txt"],
                 ["nope"], ["su"]]
        for paths in cases:
            for flags in ([], ["-r"], ["-t"], ["-d"], ["-r", "-t"]):
                with self.subTest(paths=paths, flags=flags):
                    self.assertSame("ls-tree", *flags, "HEAD", *paths)

    def test_from_subdirectories(self):
        """Paths are relative to the cwd; the cwd itself shows as ./"""
        cases = [[], ["-r"], ["-t", "-r"], ["--full-name"], ["--full-tree"], ["..", ], ["../a.txt"],
                 [".", ], ["deep/"], ["deep"], ["-r", ".."], ["--name-only", "-r"]]
        for cwd in (self.repo / "sub", self.repo / "sub" / "deep"):
            for args in cases:
                flags = [a for a in args if a.startswith("-")]
                paths = [a for a in args if not a.startswith("-")]
                with self.subTest(cwd=cwd.name, args=args):
                    self.assertSame("ls-tree", *flags, "HEAD", *paths, cwd=cwd)

    def test_not_a_tree(self):
        self.assertSame("ls-tree", "HEAD:a.txt")


class TestRevParse(GitTestCase):
    def setUp(self):
        super().setUp()
        self.sample_repo()

    def test_revisions(self):
        specs = ["HEAD", "@", "main", "heads/main", "refs/heads/main", "light", "v1", "tags/v1",
                 "v1^{}", "v1-nested^{}", "v1^{commit}", "v1^{tree}", "HEAD^", "HEAD^1", "HEAD~",
                 "HEAD~1", "HEAD~0", "HEAD^0", "HEAD^{tree}", "HEAD:sub", "HEAD:sub/deep/d.txt",
                 "HEAD~1:a.txt", "v1:a.txt", "HEAD^{commit}^{tree}"]
        for spec in specs:
            with self.subTest(spec=spec):
                self.assertSame("rev-parse", spec)
                self.assertSame("rev-parse", "--verify", spec)

    def test_abbreviated_ids(self):
        full = self.git_out("rev-parse", "HEAD").strip().decode()
        for n in (4, 7, 12, 40):
            with self.subTest(n=n):
                self.assertSame("rev-parse", full[:n])
        self.assertSame("rev-parse", "--short", "HEAD")
        self.assertSame("rev-parse", "--short=10", "HEAD")

    def test_failures(self):
        for spec in ("HEAD~5", "HEAD^2", "nope", "v1^{blob}"):
            with self.subTest(spec=spec):
                self.assertSame("rev-parse", "--verify", "-q", spec)

    def test_ref_names(self):
        self.assertSame("rev-parse", "--abbrev-ref", "HEAD")
        self.assertSame("rev-parse", "--symbolic-full-name", "HEAD")
        self.assertSame("rev-parse", "--symbolic-full-name", "main")

    def test_repo_queries(self):
        for opt in ("--git-dir", "--is-inside-work-tree", "--is-bare-repository", "--show-prefix"):
            with self.subTest(opt=opt):
                self.assertSame("rev-parse", opt)
        self.assertSame("rev-parse", "--show-prefix", cwd=self.repo / "sub")
        # --show-toplevel prints an absolute path; compare after normalizing.
        g = self.git_out("rev-parse", "--show-toplevel", cwd=self.repo / "sub").strip()
        p = self.pygit_out("rev-parse", "--show-toplevel", cwd=self.repo / "sub").strip()
        self.assertEqual(os.path.normcase(p.decode()), os.path.normcase(g.decode()))

    def test_packed_refs(self):
        self.git("pack-refs", "--all")
        self.assertSame("rev-parse", "main", "v1", "light", "v1^{}")


class TestInit(GitTestCase):
    def test_git_accepts_pygit_init(self):
        target = self.tmp / "fresh"
        self.pygit("init", "-q", str(target))
        self.assertEqual(self.git_out("rev-parse", "--is-inside-work-tree", cwd=target), b"true\n")
        self.assertEqual(self.git_out("symbolic-ref", "HEAD", cwd=target), b"refs/heads/main\n")
        self.git("status", cwd=target)
        self.git("fsck", cwd=target)

    def test_bare_and_initial_branch(self):
        target = self.tmp / "bare.git"
        self.pygit("init", "-q", "--bare", "-b", "trunk", str(target))
        self.assertEqual(self.git_out("rev-parse", "--is-bare-repository", cwd=target), b"true\n")
        self.assertEqual(self.git_out("symbolic-ref", "HEAD", cwd=target), b"refs/heads/trunk\n")

    def test_messages(self):
        g = self.git_out("init", str(self.tmp / "g"))
        p = self.pygit_out("init", str(self.tmp / "p"))
        self.assertEqual(p.replace(b"/p/", b"/g/"), g)


class TestInterop(GitTestCase):
    """pygit builds history with plumbing; git reads it, and the other way."""

    def test_pygit_built_history_passes_fsck(self):
        self.pygit("init", "-q")
        self.write("hello.txt", "hi\n")
        blob = self.pygit_out("hash-object", "-w", "hello.txt").strip()
        tree = self.pygit_out("mktree", input=b"100644 blob " + blob + b"\thello.txt\n").strip()
        c1 = self.pygit_out("commit-tree", tree.decode(), "-m", "one").strip()
        c2 = self.pygit_out("commit-tree", tree.decode(), "-p", c1.decode(), "-m", "two").strip()
        self.pygit("update-ref", "refs/heads/main", c2.decode())
        self.git("fsck", "--strict")
        self.assertEqual(self.git_out("rev-parse", "HEAD~1").strip(), c1)
        self.assertEqual(self.git_out("log", "--format=%s"), b"two\none\n")
        self.assertEqual(self.git_out("show", "HEAD:hello.txt"), b"hi\n")

    def test_commit_tree_matches_git(self):
        self.git_init()
        self.write("f", "f\n")
        self.git("add", "f")
        tree = self.git_out("write-tree").strip().decode()
        self.assertSame("commit-tree", tree, "-m", "msg")
        self.assertSame("commit-tree", tree, "-m", "para one", "-m", "para two")
        self.assertSame("commit-tree", tree, input=b"from stdin\n")

    def test_mktree_matches_git(self):
        self.sample_repo()
        listing = self.git_out("ls-tree", "HEAD")
        self.assertSame("mktree", input=listing)
        self.assertSame("mktree", "-z", input=self.git_out("ls-tree", "-z", "HEAD"))

    def test_symbolic_and_update_ref(self):
        self.sample_repo()
        head = self.git_out("rev-parse", "HEAD~1").strip().decode()
        self.pygit("update-ref", "refs/heads/other", head)
        self.assertEqual(self.git_out("rev-parse", "other").strip().decode(), head)
        self.pygit("symbolic-ref", "HEAD", "refs/heads/other")
        self.assertEqual(self.git_out("symbolic-ref", "HEAD"), b"refs/heads/other\n")
        self.assertSame("symbolic-ref", "HEAD")
        self.assertSame("symbolic-ref", "--short", "HEAD")
        self.pygit("update-ref", "-d", "refs/tags/light")
        self.assertNotEqual(self.git("rev-parse", "--verify", "-q", "light", check=False).returncode, 0)
        self.git("fsck")


if __name__ == "__main__":
    unittest.main()

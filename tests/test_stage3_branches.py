"""Stage 3: diff, branch, tag, checkout/switch, reset (compared with git)."""

import os
import unittest

from gitcompat import GitTestCase
from test_stage2_history import TwinTestCase


def snapshot(tc, d):
    """Everything a checkout can change: HEAD, index, files, status."""
    files = {}
    for dirpath, dirnames, filenames in os.walk(d):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for f in filenames:
            p = os.path.join(dirpath, f)
            files[os.path.relpath(p, d).replace("\\", "/")] = open(p, "rb").read()
    return (tc.git_out("symbolic-ref", "-q", "HEAD", cwd=d, check=False),
            tc.git_out("ls-files", "-s", cwd=d),
            files,
            tc.git_out("status", "--porcelain", cwd=d))


class Stage3Twin(TwinTestCase):
    def step(self, *args, input=None):
        g = super().step(*args, input=input)
        self.assertEqual(snapshot(self, self.p), snapshot(self, self.g), f"state differs after {args}")
        return g

    def base_history(self):
        self.write2("a", "a\n")
        self.write2("b", "b\n")
        self.write2("dir/c", "c\n")
        self.git2("add", "-A")
        self.git2("commit", "-q", "-m", "first commit")
        self.write2("a", "a\na2\n")
        self.write2("dir/d", "d\n")
        self.git2("add", "-A")
        self.git2("commit", "-q", "-m", "second")


class TestBranch(Stage3Twin):
    def test_create_list_delete(self):
        self.step("branch")
        self.base_history()
        self.step("branch", "side", "HEAD~1")
        self.step("branch", "topic")
        self.step("branch")
        self.step("branch", "-v")
        self.step("branch", "--list", "s*")
        self.step("branch", "side")
        self.step("branch", "bad..name")
        self.step("branch", "--show-current")
        self.step("branch", "-d", "side")
        self.step("branch", "-d", "nope")
        self.step("branch", "-d", "main")
        self.git2("checkout", "-q", "-b", "ahead")
        self.git2("commit", "-q", "--allow-empty", "-m", "ahead work")
        self.git2("checkout", "-q", "main")
        self.step("branch", "-d", "ahead")
        self.step("branch", "-D", "ahead")
        self.step("branch", "-m", "topic", "renamed")
        self.step("branch", "-m", "main", "trunk")
        self.step("branch", "-v")
        self.step("branch", "-c", "trunk", "copy")
        self.step("branch")

    def test_upstream(self):
        self.base_history()
        self.git2("branch", "up", "HEAD~1")
        self.step("branch", "--set-upstream-to=up")
        self.step("branch", "-vv")
        self.step("branch", "-v")
        self.step("status")
        self.step("branch", "--unset-upstream")
        self.step("branch", "--unset-upstream")
        self.step("branch", "-u", "nope")

    def test_detached_listing(self):
        self.base_history()
        self.git2("checkout", "-q", "--detach", "HEAD~1")
        self.step("branch")
        self.step("branch", "-v")


class TestTag(Stage3Twin):
    def test_tags(self):
        self.base_history()
        self.step("tag")
        self.step("tag", "light", "HEAD~1")
        self.step("tag", "-a", "v1", "-m", "annotated message\n\n# comment\nsecond line")
        self.step("tag", "v1")
        self.step("tag", "-f", "light")
        self.step("tag", "-m", "on a tag", "nested", "v1")
        self.step("tag")
        self.step("tag", "-l", "v*")
        self.step("tag", "-n")
        self.step("tag", "-n3")
        self.step("tag", "-d", "light")
        self.step("tag", "-d", "light")
        self.step("tag", "bad..name")
        for d in (self.g, self.p):
            self.git("fsck", "--strict", cwd=d)


class TestCheckout(Stage3Twin):
    def test_switching(self):
        self.base_history()
        self.git2("branch", "side", "HEAD~1")
        self.step("checkout", "side")
        self.step("checkout", "side")
        self.step("checkout", "main")
        self.step("switch", "side")
        self.step("switch", "-c", "new")
        self.step("switch", "-c", "new")
        self.step("checkout", "-b", "other", "main")
        self.step("checkout", "-B", "other", "side")
        self.step("switch", "-")
        self.step("checkout", "nope")
        self.step("switch", "HEAD~1")

    def test_detached(self):
        self.base_history()
        self.step("checkout", "HEAD~1")
        self.step("commit", "--allow-empty", "-m", "lost")
        self.step("checkout", "main")
        self.step("checkout", "--detach")
        self.step("switch", "main")
        self.step("switch", "--detach", "HEAD~1")
        self.step("status")
        self.step("checkout", "main")

    def test_local_changes(self):
        self.base_history()
        self.git2("branch", "side", "HEAD~1")
        self.write2("b", "local edit\n")
        self.step("checkout", "side")          # b is the same in both: carried
        self.step("checkout", "main")
        self.write2("a", "conflicting edit\n")
        self.step("checkout", "side")          # a differs: refused
        self.git2("add", "a")
        self.step("checkout", "side")          # staged: still refused
        self.step("checkout", "-f", "side")    # forced: discarded
        self.write2("dir/d", "untracked in the way\n")
        self.step("checkout", "main")          # untracked file would be overwritten
        os.remove(self.g / "dir" / "d")
        os.remove(self.p / "dir" / "d")
        self.step("checkout", "main")

    def test_paths(self):
        self.base_history()
        self.write2("a", "scribble\n")
        self.write2("b", "scribble\n")
        self.step("checkout", "--", "a")
        self.step("checkout", "b")
        self.step("checkout", "HEAD~1", "--", "a")
        self.step("checkout", "HEAD~1", "a", "dir")
        self.step("checkout", "--", "nope")

    def test_file_directory_swap(self):
        self.write2("x", "file\n")
        self.git2("add", "x")
        self.git2("commit", "-q", "-m", "x is a file")
        self.git2("checkout", "-q", "-b", "dir")
        self.git2("rm", "-q", "x")
        self.write2("x/inner", "inner\n")
        self.git2("add", "x")
        self.git2("commit", "-q", "-m", "x is a dir")
        self.step("checkout", "main")
        self.step("checkout", "dir")


class TestReset(Stage3Twin):
    def test_modes(self):
        self.base_history()
        self.write2("a", "dirty\n")
        self.git2("add", "a")
        self.write2("b", "unstaged\n")
        self.step("reset")
        self.git2("add", "a")
        self.step("reset", "--soft", "HEAD~1")
        self.step("reset", "main")
        self.step("reset", "--hard", "HEAD~1")
        self.step("reset", "--hard", "ORIG_HEAD")
        self.write2("a", "x\n")
        self.git2("add", "a")
        self.step("reset", "--", "a")
        self.git2("add", "a")
        self.step("reset", "HEAD~1", "--", "a")
        self.step("reset", "--hard")


class TestDiff(GitTestCase):
    def setUp(self):
        super().setUp()
        self.git_init()
        self.write("nums", "".join(f"{i}\n" for i in range(1, 31)))
        self.write("code.c", "int f(void)\n{\n\treturn 1;\n}\n\nint g(void)\n{\n\treturn 2;\n}\n")
        self.write("nonl", "x")
        self.write("sp ace", "sp")
        self.write("bin", bytes(range(256)) * 2)
        self.write("gone", "gone\n")
        self.write("café", "e")
        self.write("dir/long-file-name-for-stat-width-checking-purposes.txt",
                   "".join(f"{i}\n" for i in range(100, 161)))
        self.write("empty", "")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

    def change(self):
        self.write("nums", "".join(("five\n" if i == 5 else "twenty-five\n" if i == 25 else f"{i}\n")
                                   for i in range(1, 31)))
        self.write("code.c", "int f(void)\n{\n\treturn 10;\n}\n\nint g(void)\n{\n\treturn 2;\n}\n")
        self.write("nonl", "y")
        self.write("sp ace", "sp2\n")
        self.write("bin", bytes(range(255, -1, -1)))
        os.remove(self.repo / "gone")
        self.write("newfile", "new\n")
        self.write("café", "f")
        self.write("dir/long-file-name-for-stat-width-checking-purposes.txt",
                   "".join(f"{i}\n" for i in range(100, 301)))
        self.write("newempty", "")

    FORMS = (["diff"], ["diff", "--stat"], ["diff", "--numstat"], ["diff", "--shortstat"],
             ["diff", "--name-only"], ["diff", "--name-status"], ["diff", "-U1"], ["diff", "-U0"],
             ["diff", "--exit-code"], ["diff", "--quiet"], ["diff", "--", "nums", "code.c"],
             ["diff", "--stat=60"], ["diff", "--full-index"], ["diff", "-z", "--name-status"])

    def test_worktree_and_index(self):
        self.assertSame("diff")
        self.change()
        for args in self.FORMS:
            with self.subTest(args=args):
                self.assertSame(*args)
        self.git("add", "-A")
        for args in self.FORMS:
            cached = [args[0], "--cached"] + args[1:]
            with self.subTest(args=cached):
                self.assertSame(*cached)
        self.assertSame("diff", "HEAD", "--stat")
        self.assertSame("diff", "HEAD")

    def test_commits(self):
        self.change()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "second")
        self.git("mv", "nums", "renamed")
        self.write("code.c", "int f(void)\n{\n\treturn 10;\n}\n\nint g(void)\n{\n\treturn 3;\n}\nmore\n")
        self.git("update-index", "--add", "--chmod=+x", "code.c")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "third")
        for args in (["diff", "HEAD~2", "HEAD"], ["diff", "HEAD~1..HEAD"], ["diff", "HEAD~2", "HEAD", "--stat"],
                     ["diff", "HEAD~1", "HEAD", "--name-status"], ["diff", "HEAD~1", "HEAD", "--numstat"],
                     ["diff", "--no-renames", "HEAD~1", "HEAD", "--stat"], ["diff", "HEAD~2...HEAD"],
                     ["diff", "HEAD~1", "HEAD", "--", "code.c"], ["diff", "HEAD", "HEAD~2"],
                     ["diff", "nope"]):
            with self.subTest(args=args):
                self.assertSame(*args)

    def test_paths_from_subdirectory(self):
        self.change()
        sub = self.repo / "dir"
        for args in (["diff"], ["diff", "."], ["diff", "../nums"], ["diff", "../nums", "."],
                     ["diff", "--stat", ".."], ["diff", "HEAD", "--name-only", "--", ".."],
                     ["log", "--format=%s", "."], ["log", "--format=%s", "../nums"],
                     ["log", "--format=%s", "HEAD", "--", "."]):
            with self.subTest(args=args):
                self.assertSame(*args, cwd=sub)

    def test_type_change_and_mode(self):
        self.write("f", "f\n")
        self.git("add", "f")
        self.git("commit", "-q", "-m", "f")
        self.git("update-index", "--chmod=+x", "f")
        self.assertSame("diff", "--cached")
        self.assertSame("diff", "--cached", "--stat")
        blob = self.git_out("hash-object", "-w", "--stdin", input=b"target").strip().decode()
        self.git("update-index", "--cacheinfo", f"120000,{blob},f")
        self.assertSame("diff", "--cached")
        self.assertSame("diff", "--cached", "--name-status")


if __name__ == "__main__":
    unittest.main()

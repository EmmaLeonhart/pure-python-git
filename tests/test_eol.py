"""Line endings: core.autocrlf, core.eol and .gitattributes, against git.

Each setting is played in twin repositories (git and pygit), comparing
check-attr, the blobs `add` stores, status, diff (with its safecrlf
warnings) and the bytes checkout writes.
"""

import os
import unittest

from gitcompat import GitTestCase

FILES = {
    "crlf.txt": b"a\r\nb\r\n",
    "lf.txt": b"a\nb\n",
    "mixed.txt": b"a\nb\r\nc\n",
    "bin.dat": b"x\0y\r\n",
    "other": b"plain\nfile\r\n",
    "lonecr": b"a\rb\n",
    "sub/s.txt": b"q\r\n",
    "force.c": b"c\nline\n",
    "lf.md": b"m\r\nd\r\n",
    "auto.ini": b"k=v\r\n",
    ".gitattributes": b"*.dat binary\n*.txt text\nforce.c eol=crlf\nlf.md eol=lf\nsub/** -text\n"
                      b"*.ini text=auto\n",
}


class TestLineEndings(GitTestCase):
    def setUp(self):
        super().setUp()
        self.g, self.p = self.tmp / "g", self.tmp / "p"

    def both(self, *args):
        g = self.git(*args, cwd=self.g, check=False)
        p = self.pygit(*args, cwd=self.p, check=False)
        self.assertEqual((p.stdout, p.stderr, p.returncode), (g.stdout, g.stderr, g.returncode), f"{args}")

    def git2(self, *args):
        for d in (self.g, self.p):
            self.git(*args, cwd=d)

    def files_equal(self):
        for name in FILES:
            with self.subTest(file=name):
                self.assertEqual((self.p / name).read_bytes(), (self.g / name).read_bytes())

    def scenario(self, autocrlf, eol=None):
        for d in (self.g, self.p):
            self.git("init", "-q", str(d))
            self.git("config", "core.autocrlf", autocrlf, cwd=d)
            if eol:
                self.git("config", "core.eol", eol, cwd=d)
            for name, data in FILES.items():
                self.write(name, data, base=d)
        self.both("check-attr", "-a", *[n for n in FILES if n != ".gitattributes"], "nope")
        self.both("check-attr", "text", "crlf.txt", "bin.dat", "lf.md")
        self.both("check-attr", "eol", "text", "--", "force.c", "lf.md", "auto.ini")
        self.both("add", "-A")
        self.both("ls-files", "-s")
        self.both("status", "--porcelain")
        self.git2("commit", "-q", "-m", "one")
        for name in ("crlf.txt", "lf.txt", "force.c", "lf.md", "other", "auto.ini"):
            for d in (self.g, self.p):
                with open(d / name, "ab") as f:
                    f.write(b"z\r\n")
        self.both("status", "--porcelain")
        self.both("diff")
        self.both("diff", "--stat")
        for d in (self.g, self.p):
            for name in FILES:
                if name != ".gitattributes":
                    os.remove(d / name)
        self.both("checkout", "--", ".")
        self.files_equal()
        self.both("status", "--porcelain")
        for d in (self.g, self.p):
            for name in ("crlf.txt", "lf.txt", "force.c", "auto.ini"):
                with open(d / name, "ab") as f:
                    f.write(b"second\n")
        self.git2("commit", "-q", "-am", "two")
        self.both("checkout", "-q", "HEAD~1")
        self.files_equal()
        self.both("status", "--porcelain")

    def test_autocrlf_false(self):
        self.scenario("false")

    def test_autocrlf_true(self):
        self.scenario("true")

    def test_autocrlf_input(self):
        self.scenario("input")

    def test_core_eol(self):
        self.scenario("false", "crlf")


if __name__ == "__main__":
    unittest.main()

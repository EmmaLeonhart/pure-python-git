"""Rename detection against `git diff --no-index -M` on random file sets,
with similarity scores near and across the 50% threshold."""

import hashlib
import random
import sys
import unittest

from gitcompat import ROOT, GitTestCase

sys.path.insert(0, str(ROOT))
from pygit import diffcore  # noqa: E402


class _Odb:
    def __init__(self):
        self.blobs = {}

    def add(self, data):
        oid = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        self.blobs[oid] = data
        return oid

    def read(self, oid):
        return b"blob", self.blobs[oid]


WORDS = ["alpha", "beta", "gamma", "delta", "x = 1;", "return y;", "{", "}", "", "# note"]


class TestRenames(GitTestCase):
    def test_random_against_git(self):
        rng = random.Random(5)
        for it in range(80):
            with self.subTest(it=it):
                self._one(rng, it)

    def _one(self, rng, it):
        a, b = self.tmp / f"A{it}", self.tmp / f"B{it}"
        old, new = {}, {}
        for k in range(rng.randint(1, 4)):
            data = "".join(rng.choice(WORDS) + f" {rng.randint(0, 9)}\n"
                           for _ in range(rng.randint(1, 40))).encode()
            lines = data.split(b"\n")
            for _ in range(int(len(lines) * rng.choice([0.0, 0.2, 0.45, 0.5, 0.55, 0.8]))):
                lines[rng.randrange(len(lines))] = b"changed %d" % rng.randint(0, 999)
            name = rng.choice([f"s{k}.txt", "same.txt"])
            if name.encode() in old:
                name = f"s{k}.txt"
            old[name.encode()] = data
            new[rng.choice([f"d{k}.txt", f"sub{k}/{name}"]).encode()] = b"\n".join(lines)
        for root, files in ((a, old), (b, new)):
            for p, d in files.items():
                self.write(p.decode(), d, base=root)
        out = self.git("diff", "--no-index", "-M", "--name-status", a.name, b.name,
                       cwd=self.tmp, check=False).stdout.decode()
        want = sorted(f"{l.split(chr(9))[0][1:]} {l.split(chr(9))[1].split('/', 1)[1]} "
                      f"{l.split(chr(9))[2].split('/', 1)[1]}" for l in out.splitlines() if l.startswith("R"))
        odb = _Odb()
        om = {p: (0o100644, odb.add(d)) for p, d in old.items()}
        nm = {p: (0o100644, odb.add(d)) for p, d in new.items()}
        got = sorted("%03d %s %s" % (s, x.decode(), y.decode())
                     for x, y, s in diffcore.detect_renames(odb, om, nm))
        self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()

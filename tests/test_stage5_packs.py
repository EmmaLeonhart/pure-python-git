"""Stage 5: reading git's packs, writing packs git accepts, gc."""

import os
import unittest

from gitcompat import GitTestCase


class PackBase(GitTestCase):
    def history(self, n=30):
        """Many versions of a growing file: good delta material."""
        self.git_init()
        for i in range(1, n + 1):
            self.write("big", "".join(f"line {j}\n" for j in range(i * 15)) + f"v{i}\n")
            self.write(f"s{i % 4}", f"small {i}\n")
            if i % 7 == 0:
                self.write(f"dir{i}/nested", f"nested {i}\n" * i)
            self.git("add", "-A")
            self.git("commit", "-q", "-m", f"commit {i}")
        self.git("tag", "-a", "v1", "-m", "a tag")
        self.git("tag", "light", "HEAD~2")

    def all_objects(self):
        return self.git_out("rev-list", "--all", "--objects").decode().split("\n")


class TestReadPacks(PackBase):
    def test_everything_reads_from_git_packs(self):
        self.history()
        self.git("gc", "-q", "--aggressive")
        self.assertFalse(any(len(d) == 2 for d in os.listdir(self.repo / ".git" / "objects")))
        for line in self.all_objects():
            if not line:
                continue
            oid = line.split(" ")[0]
            with self.subTest(oid=oid):
                self.assertSame("cat-file", "-p", oid)
        for args in (["log", "--oneline"], ["log", "-3"],
                     ["ls-tree", "-r", "HEAD~4"], ["rev-parse", "v1^{}", "light", "HEAD~2"],
                     ["status"], ["diff", "HEAD~9", "HEAD", "--stat"], ["branch", "-v"], ["tag", "-n"],
                     ["count-objects", "-v"]):
            with self.subTest(args=args):
                self.assertSame(*args)

    def test_verify_pack_matches_git(self):
        self.history()
        self.git("gc", "-q", "--aggressive")
        packdir = self.repo / ".git" / "objects" / "pack"
        idx = [f for f in os.listdir(packdir) if f.endswith(".idx")][0]
        self.assertSame("verify-pack", "-v", f".git/objects/pack/{idx}")
        self.assertSame("verify-pack", "-s", f".git/objects/pack/{idx}")
        self.assertSame("verify-pack", f".git/objects/pack/{idx}")


class TestWritePacks(PackBase):
    def test_pack_objects_accepted_by_git(self):
        self.history()
        listing = self.git_out("rev-list", "--all", "--objects")
        out = self.pygit_out("pack-objects", str(self.tmp / "mine"), input=listing).strip().decode()
        pack = self.tmp / f"mine-{out}.pack"
        idx = self.tmp / f"mine-{out}.idx"
        self.assertTrue(pack.is_file() and idx.is_file())
        self.git("verify-pack", str(idx))
        verbose = self.git_out("verify-pack", "-v", str(idx)).decode()
        self.assertIn("chain length = 1", verbose)  # it really made deltas
        # git's own index of our pack is identical to ours.
        self.git("index-pack", "-o", str(self.tmp / "git.idx"), str(pack))
        self.assertEqual((self.tmp / "git.idx").read_bytes(), idx.read_bytes())
        # and pygit's index-pack of git's pack equals git's index.
        self.git("gc", "-q")
        packdir = self.repo / ".git" / "objects" / "pack"
        gpack = [f for f in os.listdir(packdir) if f.endswith(".pack")][0]
        self.pygit("index-pack", "-o", str(self.tmp / "py.idx"), f".git/objects/pack/{gpack}")
        self.assertEqual((self.tmp / "py.idx").read_bytes(), (packdir / gpack.replace(".pack", ".idx")).read_bytes())

    def test_packed_repo_is_complete(self):
        """Objects from a pygit pack, unpacked into a fresh repo by git."""
        self.history(12)
        listing = self.git_out("rev-list", "--all", "--objects")
        tips = self.git_out("for-each-ref", "--format=%(objectname)")
        out = self.pygit_out("pack-objects", "--revs", str(self.tmp / "p"), input=tips).strip().decode()
        fresh = self.tmp / "fresh"
        self.git("init", "-q", "--bare", str(fresh))
        self.git("unpack-objects", cwd=fresh, input=(self.tmp / f"p-{out}.pack").read_bytes())
        have = self.git_out("cat-file", "--batch-check=%(objectname)", "--batch-all-objects", cwd=fresh).split()
        want = [l.split(b" ")[0] for l in listing.split(b"\n") if l]
        self.assertEqual(sorted(have), sorted(want))


class TestGc(PackBase):
    def test_gc(self):
        self.history(15)
        self.git("checkout", "-q", "-b", "side")
        self.write("side", "side\n")
        self.git("add", "side")
        self.git("commit", "-q", "-m", "side")
        self.git("checkout", "-q", "main")
        self.write("staged-only", "in the index only\n")
        self.git("add", "staged-only")
        before = sorted(self.all_objects())
        self.pygit("gc")
        objects = self.repo / ".git" / "objects"
        self.assertFalse(any(len(d) == 2 for d in os.listdir(objects)), "loose objects left")
        self.git("fsck", "--strict", "--no-dangling")
        self.assertEqual(sorted(self.all_objects()), before)
        self.assertEqual(self.git_out("cat-file", "-p", ":staged-only"), b"in the index only\n")
        # packed-refs as git writes it.
        mine = (self.repo / ".git" / "packed-refs").read_bytes()
        self.git("pack-refs", "--all")
        self.assertEqual((self.repo / ".git" / "packed-refs").read_bytes(), mine)
        self.assertSame("count-objects", "-v")
        # A second gc folds the existing pack into a new one.
        self.write("more", "more\n")
        self.git("add", "more")
        self.git("commit", "-q", "-m", "more")
        self.pygit("gc")
        packs = [f for f in os.listdir(objects / "pack") if f.endswith(".pack")]
        self.assertEqual(len(packs), 1)
        self.git("fsck", "--strict", "--no-dangling")

    def test_prune_keeps_recent_unreachable(self):
        self.history(3)
        dangling = self.git_out("hash-object", "-w", "--stdin", input=b"dangling\n").strip().decode()
        self.pygit("gc")
        self.assertEqual(self.git_out("cat-file", "-t", dangling), b"blob\n")
        self.pygit("prune", "--expire=now")
        self.assertNotEqual(self.git("cat-file", "-e", dangling, check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()

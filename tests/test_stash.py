"""stash against git, in twin repositories (stash commit ids must match)."""

import unittest

from test_stage3_branches import Stage3Twin


class TestStash(Stage3Twin):
    def step(self, *args, input=None):
        g = super().step(*args, input=input)
        for ref in ("refs/stash", "refs/stash^2"):
            gs = self.git("rev-parse", "-q", "--verify", ref, cwd=self.g, check=False).stdout
            ps = self.git("rev-parse", "-q", "--verify", ref, cwd=self.p, check=False).stdout
            self.assertEqual(ps, gs, f"{ref} differs after {args}")
        return g

    def setup(self):
        self.write2("f", "1\n2\n3\n")
        self.write2("k", "k\n")
        self.git2("add", ".")
        self.git2("commit", "-q", "-m", "base commit")

    def changes(self):
        self.write2("f", "1\n2\n3\nf2\n")
        self.write2("n", "new\n")
        self.git2("add", "n")
        self.write2("k", "k\nstaged\n")
        self.git2("add", "k")
        self.write2("k", "k\nstaged\nunstaged\n")

    def test_push_list_show_apply_pop_drop(self):
        self.setup()
        self.step("stash")
        self.changes()
        self.step("stash")
        self.step("stash", "list")
        self.step("stash", "show")
        self.step("stash", "show", "-p")
        self.write2("f", "x\n")
        self.step("stash", "push", "-m", "my msg")
        self.step("stash", "list")
        self.step("stash", "apply", "stash@{1}")
        self.git2("reset", "-q", "--hard")
        for d in (self.g, self.p):
            (d / "n").unlink(missing_ok=True)
        self.step("stash", "pop", "--index", "stash@{1}")
        self.step("stash", "list")
        self.git2("reset", "-q", "--hard")
        for d in (self.g, self.p):
            (d / "n").unlink(missing_ok=True)
        self.step("stash", "drop")
        self.step("stash", "list")
        self.step("stash", "drop")
        self.step("stash", "pop")

    def test_conflicting_pop(self):
        self.setup()
        self.write2("f", "1\nmine\n3\n")
        self.git2("stash", "-q")
        self.write2("f", "1\ntheirs\n3\n")
        self.git2("commit", "-q", "-am", "c")
        self.step("stash", "pop")
        self.step("stash", "list")
        self.step("status")

    def test_include_untracked(self):
        self.setup()
        self.write2(".gitignore", "*.log\n")
        self.git2("add", ".gitignore")
        self.git2("commit", "-q", "-m", "ignore logs")
        self.write2("untracked", "u\n")
        self.write2("d/x", "x\n")
        self.write2("a.log", "ignored\n")
        self.write2("k", "k\nk2\n")
        self.step("stash", "-u")
        self.step("status", "--porcelain", "--ignored")
        for ref in ("refs/stash^3",):
            self.assertEqual(self.git_out("rev-parse", ref, cwd=self.p), self.git_out("rev-parse", ref, cwd=self.g))
        self.write2("v", "v\n")
        self.step("stash", "push", "-u")          # untracked only
        self.write2("v", "clash\n")
        self.step("stash", "pop")                 # v exists: refused, kept
        for d in (self.g, self.p):
            (d / "v").unlink()
        self.step("stash", "pop")
        self.step("stash", "pop")
        self.step("status", "--porcelain")

    def test_git_reads_pygit_stash(self):
        """A stash made by pygit is applied by git, and the other way round."""
        self.setup()
        self.changes()
        self.pygit("stash", cwd=self.p)
        self.git("stash", "-q", cwd=self.g)
        self.assertEqual(self.git_out("rev-parse", "stash", cwd=self.p),
                         self.git_out("rev-parse", "stash", cwd=self.g))
        self.git("stash", "pop", "-q", "--index", cwd=self.p)
        self.pygit("stash", "pop", "--index", cwd=self.g)
        self.assertEqual(self.git_out("status", "--porcelain", cwd=self.p),
                         self.git_out("status", "--porcelain", cwd=self.g))


if __name__ == "__main__":
    unittest.main()

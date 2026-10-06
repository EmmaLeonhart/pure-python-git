"""rebase against git, in twin repositories (rewritten ids must match)."""

import unittest

from test_stage3_branches import Stage3Twin


class TestRebase(Stage3Twin):
    def setup(self, conflict=False):
        self.write2("f", "1\n2\n3\n")
        self.git2("add", ".")
        self.git2("commit", "-q", "-m", "base")
        self.git2("checkout", "-q", "-b", "topic")
        self.write2("a", "a\n")
        self.git2("add", "a")
        self.git2("commit", "-q", "-m", "add a")
        if conflict:
            self.write2("f", "1\ntopic\n3\n")
            self.git2("commit", "-q", "-am", "topic f")
        self.write2("b", "b\n")
        self.git2("add", "b")
        self.git2("commit", "-q", "-m", "add b")
        self.git2("checkout", "-q", "main")
        self.write2("m", "m\n")
        self.git2("add", "m")
        if conflict:
            self.write2("f", "1\nmain\n3\n")
        self.git2("commit", "-q", "-am", "main work")
        self.git2("checkout", "-q", "topic")

    def test_clean(self):
        self.setup()
        self.step("rebase", "main")
        self.step("log", "--format=%H %an %s")
        self.step("rebase", "main")       # up to date

    def test_fast_forward(self):
        self.setup()
        self.git2("checkout", "-q", "-b", "behind", "main~1")
        self.step("rebase", "main")

    def test_conflict_continue(self):
        # git's --continue opens an editor on the message; "true" accepts it
        # unchanged, which is what pygit (which never opens one) does.
        self.env["GIT_EDITOR"] = "true"
        self.setup(conflict=True)
        self.step("rebase", "main")
        self.step("status")
        self.step("status", "--porcelain")
        self.step("rebase", "main")       # already in progress
        self.step("rebase", "--continue")  # unmerged
        self.write2("f", "resolved\n")
        self.git2("add", "f")
        self.step("status")
        self.step("rebase", "--continue")
        self.step("log", "--format=%H %s")
        self.step("rebase", "--continue")  # nothing in progress

    def test_conflict_abort_and_skip(self):
        self.setup(conflict=True)
        self.step("rebase", "main")
        self.step("rebase", "--abort")
        self.step("log", "--format=%H %s")
        self.step("rebase", "main")
        self.step("rebase", "--skip")
        self.step("log", "--format=%H %s")

    def test_skips_already_applied(self):
        """A commit whose change (whitespace aside) is upstream is dropped."""
        self.write2("f", "1\n2\n3\n")
        self.git2("add", ".")
        self.git2("commit", "-q", "-m", "base")
        self.git2("checkout", "-q", "-b", "topic")
        self.write2("a", "a\n")
        self.git2("add", "a")
        self.git2("commit", "-q", "-m", "add a")
        self.write2("f", "1\n2  changed\n3\n")
        self.git2("commit", "-q", "-am", "change f")
        self.write2("g", "no newline at end")
        self.git2("add", "g")
        self.git2("commit", "-q", "-m", "add g")
        self.git2("checkout", "-q", "main")
        self.write2("f", "1\n2 changed\n3\n")
        self.git2("commit", "-q", "-am", "same change, other spacing")
        self.write2("g", "no newline at end")
        self.git2("add", "g")
        self.git2("commit", "-q", "-m", "g upstream too")
        self.git2("checkout", "-q", "topic")
        self.step("rebase", "main")
        self.step("log", "--format=%H %s")

    def interactive_history(self):
        self.write2("k", "k\n")
        self.git2("add", ".")
        self.git2("commit", "-q", "-m", "base")
        for x in "abcd":
            self.write2(x, x + "\n")
            self.git2("add", x)
            self.git2("commit", "-q", "-m", f"add {x}\n\nbody of {x}")

    def interactive(self, script, *extra):
        self.env["GIT_SEQUENCE_EDITOR"] = script
        self.env["GIT_EDITOR"] = "true"
        self.step("rebase", "-i", *extra)
        self.step("log", "--format=%H %an %s%n%b")

    def test_interactive_unchanged(self):
        self.interactive_history()
        self.interactive("true", "HEAD~3")

    def test_interactive_drop_fixup_reorder(self):
        self.interactive_history()
        self.interactive("sed -i -e '1d' -e 's/^pick \\(.*add d\\)/fixup \\1/'", "HEAD~3")

    def test_interactive_squash_and_reorder(self):
        self.interactive_history()
        self.interactive("sed -i -e 's/^pick \\(.*add d\\)/squash \\1/' -e '1{h;d}' -e '/add d/G'", "HEAD~3")

    def test_interactive_squash_first(self):
        self.interactive_history()
        self.interactive("sed -i -e '1s/^pick/squash/'", "HEAD~3")
        self.step("status")
        self.step("rebase", "--abort")
        self.step("log", "--format=%H %s")

    def test_interactive_empty_todo(self):
        self.interactive_history()
        self.interactive("sed -i -e '/^pick/d'", "HEAD~2")

    def test_refusals_and_onto(self):
        self.setup()
        self.write2("a", "dirty\n")
        self.step("rebase", "main")
        self.git2("add", "a")
        self.step("rebase", "main")
        self.git2("reset", "-q", "--hard")
        self.step("rebase", "nope")
        self.step("rebase", "--onto", "main", "topic~1")
        self.step("log", "--format=%H %s")


if __name__ == "__main__":
    unittest.main()

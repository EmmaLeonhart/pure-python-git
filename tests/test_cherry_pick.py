"""cherry-pick against git, in twin repositories."""

import unittest

from test_stage3_branches import Stage3Twin


class TestCherryPick(Stage3Twin):
    def setup_history(self):
        self.write2("f", "1\n2\n3\n")
        self.write2("k", "k\n")
        self.git2("add", ".")
        self.git2("commit", "-q", "-m", "base")
        self.git2("checkout", "-q", "-b", "side")
        self.write2("f", "1\nside\n3\n")
        self.git2("commit", "-q", "-am", "side change\n\nwith a body")
        self.write2("n", "n\n")
        self.git2("add", "n")
        self.git2("commit", "-q", "-m", "add n")
        self.git2("checkout", "-q", "main")

    def test_clean_and_empty(self):
        self.setup_history()
        self.step("cherry-pick", "side")
        self.step("log", "--format=%an %ad|%s")
        self.step("cherry-pick", "side")       # now empty
        self.step("status")
        self.step("cherry-pick", "--skip")
        self.step("cherry-pick", "-x", "side~1")
        self.step("log", "--format=%B", "-1")
        self.step("cherry-pick", "--continue")  # nothing in progress

    def test_conflict_continue_and_abort(self):
        self.setup_history()
        self.write2("f", "1\nmain\n3\n")
        self.git2("commit", "-q", "-am", "main")
        self.step("cherry-pick", "side~1")
        self.step("status")
        self.step("diff")
        self.step("cherry-pick", "side")        # already in progress
        self.step("cherry-pick", "--abort")
        self.step("status")
        self.step("cherry-pick", "side~1")
        self.write2("f", "resolved\n")
        self.git2("add", "f")
        self.step("status")
        self.step("cherry-pick", "--continue")
        self.step("log", "--format=%an|%s|%b", "-1")

    def test_commit_concludes_pick(self):
        self.setup_history()
        self.write2("f", "1\nmain\n3\n")
        self.git2("commit", "-q", "-am", "main")
        self.step("cherry-pick", "side~1")
        self.write2("f", "resolved\n")
        self.git2("add", "f")
        self.step("commit", "--no-edit")
        self.step("log", "--format=%an|%s|%b", "-1")

    def test_sequence(self):
        self.setup_history()
        self.write2("f", "1\nmain\n3\n")
        self.git2("commit", "-q", "-am", "main")
        self.step("cherry-pick", "side~1", "side")
        self.step("status")
        self.write2("f", "fixed\n")
        self.git2("add", "f")
        self.step("cherry-pick", "--continue")
        self.step("log", "--format=%s", "-3")
        self.step("cherry-pick", "side~1", "side")
        self.step("cherry-pick", "--abort")
        self.step("log", "--format=%s", "-3")

    def test_refusals(self):
        self.setup_history()
        self.write2("n", "dirt\n")
        self.git2("add", "n")
        self.step("cherry-pick", "side")
        self.git2("reset", "-q", "--hard")
        self.write2("n", "untracked\n")
        self.step("cherry-pick", "side")
        for d in (self.g, self.p):
            (d / "n").unlink()
        self.git2("merge", "-q", "--no-ff", "--no-edit", "side")
        self.git2("checkout", "-q", "-b", "other", "HEAD~1")
        self.step("cherry-pick", "main")
        self.step("cherry-pick", "nope")


if __name__ == "__main__":
    unittest.main()

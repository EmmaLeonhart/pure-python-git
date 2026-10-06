"""revert against git, in twin repositories.

git opens an editor for the revert message; GIT_EDITOR=true accepts it
unchanged, which is what pygit (which never opens one) does.
"""

import unittest

from test_stage3_branches import Stage3Twin


class TestRevert(Stage3Twin):
    def setUp(self):
        super().setUp()
        self.env["GIT_EDITOR"] = "true"

    def setup_history(self):
        self.write2("f", "1\n2\n3\n")
        self.git2("add", ".")
        self.git2("commit", "-q", "-m", "base")
        self.write2("f", "1\ntwo\n3\n")
        self.git2("commit", "-q", "-am", "change two")
        self.write2("n", "n\n")
        self.git2("add", "n")
        self.git2("commit", "-q", "-m", "add n")

    def test_clean_empty_and_no_commit(self):
        self.setup_history()
        self.step("revert", "HEAD")
        self.step("log", "--format=%B", "-1")
        self.step("revert", "HEAD~1")       # reverting "add n" again: empty
        self.git2("reset", "-q", "--hard")
        self.step("revert", "-n", "HEAD~1")
        self.step("status")
        self.step("revert", "--continue")   # nothing in progress

    def test_conflict(self):
        self.setup_history()
        self.write2("f", "1\nTWO\n3\n")
        self.git2("commit", "-q", "-am", "again")
        self.step("revert", "HEAD~2")
        self.step("status")
        self.step("revert", "HEAD")          # already in progress
        self.write2("f", "r\n")
        self.git2("add", "f")
        self.step("status")
        self.step("revert", "--continue")
        self.step("log", "--format=%an|%B", "-1")
        self.step("revert", "HEAD~3")
        self.step("revert", "--abort")
        self.step("revert", "HEAD~3")
        self.write2("f", "x\n")
        self.git2("add", "f")
        self.step("commit", "--no-edit")
        self.step("log", "--format=%B", "-1")

    def test_sequence(self):
        self.setup_history()
        self.step("revert", "HEAD", "HEAD~1")
        self.step("log", "--format=%s", "-3")


if __name__ == "__main__":
    unittest.main()

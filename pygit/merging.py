"""Merge state. Stage 4 fills this in; until then there is never a merge in progress."""

from __future__ import annotations


def merge_state_lines(repo, st) -> list[bytes]:
    """Lines `git status` prints about an in-progress merge."""
    return []

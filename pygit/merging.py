"""Merge state: what `git status` says while a merge is in progress."""

from __future__ import annotations


def in_merge(repo) -> bool:
    return (repo.gitdir / "MERGE_HEAD").is_file()


def merge_state_lines(repo, st) -> list[bytes]:
    """show_merge_in_progress: the paragraph after the branch line."""
    if not in_merge(repo):
        return []
    if st.unmerged:
        return [b"You have unmerged paths.\n",
                b'  (fix conflicts and run "git commit")\n',
                b'  (use "git merge --abort" to abort the merge)\n',
                b"\n"]
    return [b"All conflicts fixed but you are still merging.\n",
            b'  (use "git commit" to conclude merge)\n',
            b"\n"]


def resolution_hint(unmerged_codes) -> bytes:
    """The "(use ... to mark resolution)" line for the Unmerged paths section,
    by git's stage-mask rules (DD = only stage 1; UD/DU = 1+2 or 1+3)."""
    both_deleted = any(c == "DD" for c in unmerged_codes)
    del_mod = any(c in ("UD", "DU") for c in unmerged_codes)
    not_deleted = any(c not in ("DD", "UD", "DU") for c in unmerged_codes)
    if not both_deleted:
        if not del_mod:
            return b'  (use "git add <file>..." to mark resolution)\n'
        return b'  (use "git add/rm <file>..." as appropriate to mark resolution)\n'
    if not del_mod and not not_deleted:
        return b'  (use "git rm <file>..." to mark resolution)\n'
    return b'  (use "git add/rm <file>..." as appropriate to mark resolution)\n'

"""Merge state: what `git status` says while a merge is in progress."""

from __future__ import annotations


def in_merge(repo) -> bool:
    """A merge or cherry-pick is in progress (git prints no unstage hints then)."""
    return (repo.gitdir / "MERGE_HEAD").is_file() or (repo.gitdir / "CHERRY_PICK_HEAD").is_file()


def merge_state_lines(repo, st) -> list[bytes]:
    """show_merge_in_progress / show_cherry_pick_in_progress: the paragraph
    after the branch line."""
    pick = repo.gitdir / "CHERRY_PICK_HEAD"
    if pick.is_file() or (repo.gitdir / "sequencer").is_dir() and not (repo.gitdir / "MERGE_HEAD").is_file():
        from pygit import revparse
        lines = []
        if pick.is_file():
            short = revparse.short_id(repo, pick.read_text().strip())
            lines.append(f"You are currently cherry-picking commit {short}.\n".encode())
        else:
            lines.append(b"Cherry-pick currently in progress.\n")
        if st.unmerged:
            lines.append(b'  (fix conflicts and run "git cherry-pick --continue")\n')
        elif not pick.is_file():
            lines.append(b'  (run "git cherry-pick --continue" to continue)\n')
        else:
            lines.append(b'  (all conflicts fixed: run "git cherry-pick --continue")\n')
        lines.append(b'  (use "git cherry-pick --skip" to skip this patch)\n')
        lines.append(b'  (use "git cherry-pick --abort" to cancel the cherry-pick operation)\n')
        lines.append(b"\n")
        return lines
    if not (repo.gitdir / "MERGE_HEAD").is_file():
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

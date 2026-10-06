# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

## History commands, from todo.md

2. `cherry-pick <commit>...`: a merge with the commit's parent as base,
   the original author and message, `-x`, `-n`, conflicts with
   CHERRY_PICK_HEAD, `--continue`, `--abort`, `--skip`; status lines
   during a cherry-pick.
3. `stash` (`push`/no subcommand, `-m`, `list`, `show`, `pop`, `apply`,
   `drop`, `clear`): stash commits shaped as git makes them (index and
   work-tree commits, refs/stash with its reflog), so git can read them.
4. `rebase <upstream>` (non-interactive): replay commits with the
   cherry-pick machinery, `--continue`/`--abort`/`--skip`, the
   `.git/rebase-merge` state git uses, and its messages.
5. Python 3.9: still unchecked (no interpreter here; CI blocked on GitHub
   billing, see INTENT.md).

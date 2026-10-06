# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

1. Run the full test suite on the commits since 09:49 (`revert`, patch-id,
   `stash -u`, `-m`, `rebase -i`). The 09:3x run was
   stopped by Claude Code because the machine was critically low on
   memory; only the revert and cherry-pick tests have run. BLOCKED-ON-USER-ACTION:
   rerun when the user says memory is available (Claude Code asks not to
   restart it unprompted).
2. Python 3.9: still unchecked (no interpreter here; CI blocked on GitHub
   billing, see INTENT.md).

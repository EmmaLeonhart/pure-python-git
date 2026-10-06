# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

1. Run the full test suite on the `revert` commit. The 09:3x run was
   stopped by Claude Code because the machine was critically low on
   memory; only the revert and cherry-pick tests have run. BLOCKED-ON-USER-ACTION:
   rerun when the user says memory is available (Claude Code asks not to
   restart it unprompted).
2. Rebase: drop commits whose patch is already upstream (patch-id), with
   git's "skipped previously applied commit" warning.
3. Python 3.9: still unchecked (no interpreter here; CI blocked on GitHub
   billing, see INTENT.md).

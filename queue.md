# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

## Stage 4: merging

1. `merge-base` command (`--all`, `--is-ancestor`, `--octopus` not needed),
   checked against git on criss-cross histories (several best bases).
2. Three-way file merge (`merge-file`): a port of git's xmerge (conflict
   regions from two xdiff scripts against the base, `<<<<<<< ours`,
   `=======`, `>>>>>>> theirs` markers with git's labels, zealous
   simplification of conflict hunks, identical changes on both sides
   merged cleanly). Tested byte for byte against `git merge-file -p`.
3. Three-way tree merge (the "ort" strategy's results for the common
   cases): clean merges of non-overlapping changes, content conflicts,
   modify/delete, add/add, rename detection on both sides, file/directory
   conflicts; index stages 1-3 for conflicts; the work tree gets the
   merged files with markers.
4. `merge <branch>`: "Already up to date.", fast-forward (`Updating a..b`
   / `Fast-forward` + diffstat), `--ff-only`, `--no-ff`, merge commit with
   message "Merge branch 'x'", conflicts ("CONFLICT (content): ..." and
   "Automatic merge failed; fix conflicts and then commit the result."),
   MERGE_HEAD/MERGE_MSG/ORIG_HEAD, refusing to merge over local changes.
5. `merge --abort` (and `--continue`), status during a merge ("You have
   unmerged paths."/"All conflicts fixed but you are still merging."),
   committing a resolved merge, `diff` of unmerged paths.
6. README design section for stage 4; tests against git for each case.

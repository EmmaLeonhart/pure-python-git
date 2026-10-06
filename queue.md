# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

## Stage 2: the index

1. Index read/write (`.git/index`, version 2, with the trailing SHA-1;
   extensions skipped on read and dropped on write, except that a `TREE`
   cache is never written stale). Round-trip test against git-written indexes.
2. `ls-files` (`-s`, `--stage`) to check the index byte for byte against git.
3. `write-tree` from the index; matches git's tree ids.
4. `.gitignore` rules: per-directory `.gitignore`, `.git/info/exclude`,
   `core.excludesFile`; negation, `/` anchoring, `**`, trailing `/` for
   directories. Test with `check-ignore` against git.
5. `add` (paths, directories, `-A`, `.`), `rm` (`--cached`, `-r`), updating
   stat data so git sees a clean index.
6. `status` (long format and `--short`/`--porcelain`): staged, unstaged,
   untracked, deleted; byte-identical to git where git defines the format
   (`--porcelain`).
7. `commit` (`-m`, `-a`, `--allow-empty`, root commits), HEAD and reflog
   update, output line `[branch id] subject`.
8. `log` (default format, `--oneline`, `--format` basics, `-n`), matching git
   for linear and merge histories.
9. Interop: git commits on a pygit-built index and the other way round;
   `git fsck` and `git status` clean. README design section for stage 2.

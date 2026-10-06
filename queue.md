# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

## Stage 2: the index (continued)

1. Myers diff and `--stat`-style counts, pulled forward from stage 3:
   `commit` prints a summary (`N files changed, X insertions(+)`, `create
   mode` lines) that needs line counts. Plan change recorded here on
   2026-10-06: the diff engine is built now, the `diff` command stays in
   stage 3.
2. `commit` (`-m`, `-F`, `-a`, `-q`, `--allow-empty`, `--amend`, root
   commits), HEAD and reflog update, message cleanup, output byte-identical
   to git (`[branch (root-commit) id] subject` plus the summary); "nothing
   to commit" prints status and exits 1.
3. `log` (default format, `--oneline`, `--format`/`--pretty` placeholders,
   `-n`, `--reverse`, revision ranges `a..b`), matching git for linear and
   merge histories.
4. Interop tests: git commits on a pygit-built index and the other way round;
   `git fsck` and `git status` clean. README design section for stage 2.

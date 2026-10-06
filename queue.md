# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

## Stage 3: branches, tags, checkout/switch, diff

1. `diff`: work tree vs index (default), `--cached`/`--staged` (index vs
   HEAD or a commit), `<commit>` (work tree vs commit), `<a> <b>` and
   `a..b` (commit vs commit). Headers byte-identical to git: `diff --git`,
   `index` (abbreviated ids and mode), new/deleted file mode, old/new mode,
   similarity index and rename from/to, `Binary files ... differ`,
   `\ No newline at end of file`. Options: `-U<n>`, `--stat`, `--numstat`,
   `--shortstat`, `--name-only`, `--name-status`, `--no-renames`,
   `--quiet`/`--exit-code`, pathspecs.
2. `branch`: list (current marked `*`, `-v`, `-a`, `-r`), create (at HEAD or
   a start point), `-d`/`-D` (with the "not fully merged" check), `-m`/`-M`,
   `--set-upstream-to`, `--unset-upstream`, `--show-current`; messages
   identical to git.
3. `tag`: list (with `-l` patterns), lightweight, annotated (`-a -m`,
   `-F`), `-d`, `-f`, `-n` output.
4. Two-tree checkout engine: move the work tree and index from one commit
   to another, refusing when local changes would be overwritten (git's
   messages), keeping unrelated local changes, removing emptied
   directories.
5. `switch` and `checkout` (branch, `-b`/`-c`, `-B`, `--detach`, a commit
   with git's detached-HEAD advice, `checkout [<rev>] -- <paths>`), reflog
   messages `checkout: moving from X to Y`, "Switched to ..." and tracking
   output.
6. `reset` (`--soft`, `--mixed`, `--hard`, `reset -- paths`), needed by
   stage 4's `merge --abort` and by tests; `ORIG_HEAD`.
7. README design section for stage 3; tests for every command against git.

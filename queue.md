# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

## Stage 5: packfiles and local transport (continued)

1. Local transport: `clone <path>` (objects copied as a pack, refs mapped to
   `refs/remotes/origin/*`, `origin` remote config, `HEAD` checkout,
   `--bare`), `fetch` (negotiating with the local repository's refs,
   updating remote-tracking refs, FETCH_HEAD, output lines git-style),
   `push` (fast-forward checks, non-fast-forward rejection, creating and
   deleting remote branches, refusing to update a checked-out branch).
   Also `remote add/-v`, `pull` (fetch + merge).
2. README design section for stage 5 and a final pass over the limits.

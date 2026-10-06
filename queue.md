# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

## Stage 5: packfiles and local transport

1. Pack reading: `.pack` v2 files and `.idx` v2 indexes (fanout, sorted ids,
   CRC32, 4- and 8-byte offsets), all object types including `OFS_DELTA`
   and `REF_DELTA`, delta application, a cache of resolved bases. Plugs into
   `ObjectStore` (`pygit/pack.py`, `load_packs`), so every command reads
   packed repositories. Tests: run the whole suite's sample repos after
   `git gc`, and `verify-pack`-style checks.
2. `verify-pack -v` and `index-pack` output byte-identical to git (or as
   close as git defines it: object ids, types, sizes, offsets).
3. Pack writing: `pack-objects` with git's object order, delta compression
   (a sliding window of candidates, git-style delta encoding with copy and
   insert instructions), `.idx` v2 generation; git's `verify-pack` and
   `fsck` accept the result.
4. `gc`: pack all reachable objects (and refs into `packed-refs`), remove
   loose objects that are now packed, `prune` unreachable loose objects
   older than the grace period.
5. Local transport: `clone <path>` (objects copied as a pack, refs mapped to
   `refs/remotes/origin/*`, `origin` remote config, `HEAD` checkout,
   `--bare`), `fetch` (negotiating with the local repository's refs,
   updating remote-tracking refs, FETCH_HEAD, output lines git-style),
   `push` (fast-forward checks, non-fast-forward rejection, creating and
   deleting remote branches, refusing to update a checked-out branch).
   Also `remote add/-v`, `pull` (fetch + merge).
6. README design section for stage 5 and a final pass over the limits.

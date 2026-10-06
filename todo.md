# todo.md: long-horizon stages

Abstract destinations, from `data_lake/brief.md`. Each is broken into concrete
steps in `queue.md` when work on it begins.

- **Stage 2: the index.** Binary index format v2, `add`, `rm`, `status`,
  `commit`, `log`, `.gitignore` rules.
- **Stage 3: branches and diff.** Branches and tags, `checkout`/`switch`,
  Myers diff with unified output between work tree, index and commits.
- **Stage 4: merging.** Merge bases, fast-forward, three-way merge of trees
  and file contents with conflict markers, `merge --abort`.
- **Stage 5: packfiles and transport.** Read and write packs and pack indexes
  (with deltas), `gc`, `clone`/`fetch`/`push` between local repositories.
- **Design doc.** Keep README's design-and-limits section current per stage.

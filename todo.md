# todo.md: long-horizon work

The five stages of `data_lake/brief.md` are done (see `devlog.md`). What
remains is closing the limits listed in README.md, most valuable first:
the ones where pygit's output can differ from git's on ordinary use.

- **History editing, remaining options.** `rebase -i`, `cherry-pick -m` /
  `revert -m`.
- **Pack fidelity.** Thin packs in `index-pack`, `.rev` files, `gc --auto`.

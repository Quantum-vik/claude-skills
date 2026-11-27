# Fixing check results

`check` derives one rule per import that differs between the fork point and the source:

| Report | Meaning | Rule |
|---|---|---|
| `new A imports X, which only exists after B changes` | A's new code uses something B adds | PR(B) ≤ PR(A) |
| `old A imports X, which B removes` | A's old code uses something B deletes | PR(A) ≤ PR(B) |

An order problem prints the two files and their PRs: the left one must move to the same or an earlier PR than the
right one (or the right one later).

## Order problem

Cheapest first:
1. Move the smaller file to the other file's PR (or next to it).
2. Move the removal later: put a deleted file, or the PR that deletes the symbol, after its last old user changes.
3. Expand/contract (below), when moving would break the cap or create a cycle.

## Cycle

A cycle means the files need each other both ways (usually: B removes something old A uses, and new A uses
something B adds). Options:
- **Same PR** for all files in the cycle, if the size allows.
- **Expand/contract**: split the file that both adds and removes. Its *expand* version (new symbols added, old
  ones kept) lands early; the *contract* change (old symbols removed) lands in a later PR after every old user has
  moved. Make the expand version by hand on the earlier branch after `cut`, then `sync`; the later PR then shows
  only the removal.
- **Review slices**: if the user accepts that middle branches don't run, the cycle only matters for reading
  order; keep the files where a reviewer understands them best and record the cycle in the document.

## Migrations and schema

The checker cannot see the database. Rules:
- A migration lands with (or before) every model change it reflects, and every piece of code that reads or
  writes a renamed or dropped column/table must land in the same PR as the migration, or the branches in between
  break at runtime.
- To keep that PR small, split the migration: *add* (new tables and columns, nullable) early, code switches in
  the middle PRs, *drop* (old columns and tables) last. Only do this if the user accepts more than one migration
  file.

## Over the cap

- **Moved code** (a function moved to a new file): counted twice although the reviewer reads it once. Either
  accept it and tell reviewers to use `git diff --color-moved`, or split into a pure-move PR and a change PR.
- **Rewritten test files:** usually accepted as test-only exceptions; splitting one test file is artificial.
- **Mostly deletions** in a kept file: often fine; say so in the PR card.
- Otherwise split by sub-feature (one endpoint, one rule family) rather than by file type.

Record every accepted exception, and why, in the stack document.

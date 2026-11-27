# Plan file

`.stack/stack.toml` (any path with `--plan`). `stack.py init` writes a skeleton.

```toml
base = "origin/main"                 # what PR 1 targets; the fork point is merge-base(base, source)
source = "a1b2c3d"                   # branch or commit holding the whole change (`snapshot` for a staged index)
branch_prefix = "feature/ABC-123-"   # branch N = <prefix><NN>-<name>
cap = 700                            # max added + deleted lines per PR, whole-file deletions not counted
exclude = ["config/local.py", ".env*"]   # changed paths that stay out of every PR

[[pr]]
name = "data-model"                  # lowercase-dashed; part of the branch name
title = "[ABC-123] feat: add binding tables"   # commit subject and PR title, in the repo's format
purpose = "New tables and the migration."      # optional; shown in the document
check = "pytest app/tests/test_models.py"      # optional; shown in the document
paths = ["app/models.py", "app/migrations/0008*", "app/engine/"]
```

## Path patterns

| Pattern | Matches |
|---|---|
| `app/models.py` | that file |
| `app/engine/` (trailing slash) | everything under the folder |
| `app/migrations/0008*`, `tests/test_*.py` | shell-style glob (`fnmatch`; `*` also crosses `/`) |

A file belongs to the PR with the **longest** matching pattern, so a folder can go to one PR while single files
inside it go to others. An `exclude` match wins over everything. Every changed file must match something;
`check` lists the ones that don't.

## How sizes are counted

`git diff --numstat --no-renames <fork> <source>`: for files that still exist, added + deleted lines count
against the cap; a deleted file's lines are reported separately and not counted. A rename is a delete plus an
add.

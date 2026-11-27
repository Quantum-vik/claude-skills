---
name: stacked-prs
description: Split one large change (a staged index, a branch or a commit) into a stack of small dependent PRs, each under a line cap, and keep the stack healthy afterwards. Plans the layers, checks Python import order and dependency cycles between PRs, writes the stack document (map, per-PR cards, file-to-PR index, change procedure), cuts the branches without touching the working tree, verifies each branch, and merges fixes upward. Use when asked for stacked PRs, to split a big PR or feature into smaller PRs, to plan PRs under N lines, or to fix, restack or document an existing stack.
---

# Stacked PRs

One large change → N small PRs, each based on the one below it. On GitHub each PR shows only its own layer;
each branch holds everything below it, so the top branch is always the whole feature.

```
base (release / main or an integration branch)
 └── <prefix>01-<name>   PR 1  base: base
      └── <prefix>02-<name>   PR 2  base: 01
           └── …              PR N  base: N-1   ← top branch == the whole change
```

Kit (paths relative to this skill's folder, `~/.claude/skills/stacked-prs/`):

| File | Role |
|---|---|
| `scripts/stack.py` | `snapshot`, `init`, `check`, `doc [--prs]`, `cut`, `verify`, `sync` (run with `--help`) |
| `reference/plan-format.md` | The plan file (`.stack/stack.toml`) and how paths match |
| `reference/fixing.md` | How to resolve order problems, cycles, migrations and over-cap PRs |

Needs `git` and `python3` ≥ 3.11 (stdlib only). Pushing and PR creation need `gh`.

## Ground rules

- Never push, open PRs, or delete branches without the user's explicit go in that turn. `cut` and `sync` create
  local branches and merge commits only; say so before running them.
- Nothing here touches the user's working tree or index: `cut` uses a temporary index, `verify` and `sync` use
  temporary worktrees.
- Keep the plan and the generated document out of the feature: put `.stack/` in `.git/info/exclude`.
- Follow the repo's own branch-name and commit-subject rules (check CI config, CONTRIBUTING, AGENTS.md); put them
  in `branch_prefix` and each `title`.

## Steps

1. **Settle the inputs with the user.** Base branch (fetch first and use the remote-tracking ref, for example
   `origin/main`; a stale local branch makes the diff include other people's work), the line cap, whether PRs
   target the base directly or an integration branch, and which local files stay out (`exclude`). Done when all
   four are known.
2. **Fix the source.** A branch or commit works as is. Work that is only staged: `stack.py snapshot` prints a
   commit sha of the index without moving any branch; use it as `source`. Re-snapshot whenever the work changes.
3. **Draft the plan.** `stack.py init` writes `.stack/stack.toml` and lists every changed file with its size.
   Group files into layers bottom-up: shared/core changes → data model and migrations → domain logic → APIs →
   background jobs → tests → removal of old code. One PR = one reviewable idea; tests go in the PR of the code they
   test, or right after it when the cap forces it.
4. **Check until clean.** `stack.py check` reports unassigned files, each PR's size against the cap, and, for
   Python, import order problems and cycles with the exact symbol that causes each. Fix them with
   `reference/fixing.md` and re-run. Decisions that change the plan go to the user, notably the **stack type**:
   - *review slices*: only the top branch must run; middle PRs exist to be read (cheap, needs an integration
     branch, middle CI may be red);
   - *runnable*: every branch passes its checks (needs expand/contract splits and sometimes split migrations).
   Show the user the size table and the open problems, and wait for approval. Done when `check` prints `OK`, or
   every remaining problem is accepted by the user and recorded in the document.
5. **Write the document.** `stack.py doc --out <path>` generates, from git alone:
   - a *Read me first* guide for any later session, the *starting state* (base, fork point, source sha, branches
     cut and whether the top matches the source, stack type, exclusions), the branch tree and the stack map;
   - *problems and decisions* (over-cap PRs, open `check` problems, your `notes`);
   - one card per PR: title, branch and base, size, purpose, check, **lands after** (each earlier PR with the exact
     imports that force the order; `⚠` marks a dependency on a later PR) and **used by**, plus a per-file
     **what changed** table: symbols added / removed / changed with line ranges and +/− lines, changed signatures
     and class methods, code moved between files (`unchanged` or `then +a/−b`), import changes, and changed line
     ranges for non-Python files;
   - the file → PR index and the change procedure.
   Fill `purpose` and `check` per PR and `stack_type` / `notes` in the plan (they are rendered into the document),
   then regenerate; never hand-edit the generated file, or the next regeneration loses it. Put it where the repo
   keeps design docs, or ask. Done when no `TODO` remains. After the PRs are opened, regenerate with `--prs` to
   fill the PR numbers and states (needs `gh`).
6. **Cut (after approval).** `stack.py cut` creates the N branches from the fork point with one commit each and
   confirms the top branch equals the source. It refuses if a branch already exists.
7. **Verify.** `stack.py verify --cmd "<build/test command>"` runs the command in a fresh worktree of each branch
   (`--from N` to start later). For review slices, verify the top branch at least. Untracked config a command
   needs must be copied in by the command itself.
8. **Publish (only on the user's go).** For each branch in order: `git push -u origin <branch>`, then
   `gh pr create --base <branch below, or base for PR 1> --head <branch> --title "<title>" --body-file <file>`,
   where the body is the stack map plus that PR's card from the document. Then add each PR's number to the map.

## Picking up an existing stack (new session)

Read the stack document first, not the code: *Read me first*, *Starting state*, then the card of the PR you are
working on. Its *what changed* table says which symbols changed and where; open a file only for logic you need,
and skip code marked `moved … unchanged`. Confirm it is current: the source sha in the header must match the plan's
`source`, and if branches exist the starting state must say the top branch matches. Otherwise regenerate it.

## Keeping the stack healthy

- **Change in PR k:** commit on branch k, then `stack.py sync k` merges k into k+1, k+1 into k+2, … to the top.
  It merges, never rebases, and stops on a conflict with the worktree path to resolve in.
- **Merging:** bottom-up. With "delete branch on merge", GitHub retargets the next PR to the base automatically.
  Use merge commits; a squash merge rewrites the layer and every PR above it then shows conflicts.
- **New file:** add it to the plan under the first PR that needs it, `check`, commit it on that branch, `sync`.
- **Moving files between PRs** after the branches exist: before anything is pushed, delete the local branches
  from that PR up and `cut` again; after pushing, only with the reviewers' agreement.

## Limits of the checker

- Import analysis is Python only. For other languages rely on `verify` with the build command.
- It sees module and top-level names, not changed signatures, string references (settings, URL includes, task
  names, dependency-injection keys) or database schema. Migrations need the rule in `reference/fixing.md`.

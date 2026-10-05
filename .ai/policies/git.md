# Git Policy

## Model
ONE TASK = ONE ISSUE + ONE BRANCH + (optionally) ONE WORKTREE + ONE primary agent per worktree.
Never run two agents in the same worktree. Never touch another developer's worktree.

## Branches
`feature/<issue-id>-<short-name>`, `fix/…`, `refactor/…`, `docs/…`, `chore/…`.
`main` is protected: no direct pushes, no force pushes. Changes land by reviewed PR.
If the environment dictates a branch name (e.g. a hosted agent), use it, and note it in the PR.

## Before you change anything
`git status`. If the tree is dirty: inspect the changes, do not discard, overwrite,
reset, or auto-stash them. Commit only what belongs to your task.

## Prohibited without explicit human authorization
`git reset --hard`, `git clean -fd`/`-fdx`, `git checkout -- <path>` or `git restore`
over changes you did not make, `git push --force`, `git push --force-with-lease`,
deleting branches you did not create, rebasing/amending shared history,
`git filter-branch`/`filter-repo`, `--no-verify` to skip hooks.

## Commits
Conventional Commits: `type(scope): summary` — `feat`, `fix`, `refactor`, `docs`,
`test`, `chore`, `ci`, `perf`, `build`. Imperative mood, explain the why in the body.
One logical change per commit. No cleanup mixed into feature commits.

## Worktrees
```
git worktree add ../<repo>-<issue-id> -b feature/<issue-id>-<short-name> origin/main
git worktree remove ../<repo>-<issue-id>   # after the PR is merged
```
Keep branches short-lived; merge/rebase from `main` using your team's convention,
but never rewrite a branch others have pulled.

## Merging
Agents never merge their own PRs and never claim approval. A human reviewer approves.

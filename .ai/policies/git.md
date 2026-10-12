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
Agents never merge their own PRs by hand and never claim approval. A human reviewer approves.

**Exception chosen by the repository owner: the staged release process.** `scripts/ship.py merge` (and `all`) may merge the
pull request of its own branch, and only when all of these hold on the exact head commit: every check reported for it is
`success`, `neutral` or `skipped` (and at least one check was reported); the pull request is open, not a draft and mergeable;
and no reviewer's latest verdict is "changes requested". The merge is pinned to that head sha, so a push after the checks cannot
be merged by mistake. The process never pushes to `main`, never skips or edits a check to make it pass, and never changes branch
protection or CI permissions: those stay with the owner.

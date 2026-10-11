# Implementation Plan: A staged release process, and removing the hosted-platform deployment path

- Issue: none yet (unknown)
- Author / agent: Claude Code
- Risk class: HIGH RISK (CI/CD process, deployment, merge policy, deletion of a deployment path)
- Date: 2026-10-11
- Status: decisions answered on 2026-10-11 (1: amend `AGENTS.md`; 2: delete the history too; 3: publish automatically after the
  merge); implemented in the pull request that carries this file. Record: [ADR 0010](../decisions/0010-staged-release-process.md).

## Objective

1. Every change reaches the system through the same nine steps: test locally, verify, open a pull request, wait for the
   checks, verify they are green, fix what is red, repeat until green, merge to `main`, publish to the system link
   (the dashboard on the VPS).
2. The deployment path for a single hosted-container service (one service, one volume) is removed from the whole system: the
   repository and the VPS. The server is deployed with the Compose files in `docker/` ([deployment-vps.md](../deployment-vps.md)).

## Context and constraints

| Fact | Evidence |
|---|---|
| The hosted-platform path was 5 files (its configuration, start script, runtime configuration, guide and decision record) and 178 lines in about 20 others: the Dockerfile, the deploy-files tests, the README, installation, architecture, development and assumptions pages, the first implementation plan, the changelog, and a comment each in `src/agentlab/storage/db.py` and `tests/unit/test_storage.py`. | `git grep` for the platform's name |
| The VPS had **no service, CLI, environment variable, cron job, systemd unit, Docker volume or container** of that platform. The only trace was two files inside the running image, copied there by the Dockerfile. Other matches were old build logs, a stale scratch copy of this repository under `/tmp`, and the unrelated `simple-rag` repository's own platform file. | `find`, `docker`, `crontab`, `systemctl` on 2026-10-11 |
| The CI workflow (`.github/workflows/ci.yml`) has four jobs: `python (lint, types, tests)`, `web (types, tests, build)`, `image (docker build)` and `validate-governance`. | the workflow file and the check runs of PR 5 |
| The server has no GitHub credentials. A pull request, its checks and its merge need the GitHub API; pushing needs a credential too. `gh` is not installed. | earlier session |
| `AGENTS.md` section 8: "Never merge your own work, bypass review". Step 8 of the requested process merges. | `AGENTS.md` |
| `AGENTS.md` section 13: changing repository security settings and CI permissions needs explicit human authorization. | `AGENTS.md` |
| ADRs are append-only history ("supersede, don't rewrite", `docs/decisions/README.md`). | `docs/decisions/README.md` |

## Proposed approach

### A. The release script, `scripts/ship.py` (new, in the repository)

One script with one sub-command per stage, so a person or an agent runs the same steps and nobody has to remember them.

| Step | Command | What it does | Stops when |
|---|---|---|---|
| 1-2 test locally, verify | `scripts/ship check` | Exactly what CI runs: `ruff check .`, `ruff format --check .`, `mypy src`, `python scripts/export_openapi.py --check`, `pytest tests -m "not docker and not browser and not postgres and not redis and not ollama and not matrix"`, `python3 scripts/ai/validate_governance.py`, the web job (`npm run gen:api` must not change `schema.d.ts`, `npm test`, `npm run build`, in a `node:22` container), and `docker build` of the image. It also checks that the tree is clean and the branch is not `main`. | any check fails |
| 3 open the pull request | `scripts/ship pr` | Pushes the branch (HTTPS, token from a file, never in the remote URL), opens the pull request (or finds the open one) with a description taken from the commits. | the branch is `main`, or `check` was not run on this commit |
| 4-5 wait, verify | `scripts/ship wait` | Polls the check runs of the pull request head until every one is completed. Prints each check and its conclusion, any unresolved review thread, and the merge state. | a check is not `success`, a review requests changes, or the head is not mergeable |
| 6 resolve | (a person or an agent) | `scripts/ship wait` prints the failing job's log tail so the cause is visible. Reproduce it locally with `scripts/ship check`, fix, commit. | |
| 7 loop | `scripts/ship check && scripts/ship pr && scripts/ship wait` | Runs again on the new head. Repeat until `wait` exits 0. A run that has not changed after a push is a stalled check, not a pass. | |
| 8 merge | `scripts/ship merge` | Merges only if every check on the **current head** is `success`, the pull request is mergeable, and no review requests changes. Uses a merge commit and pins the head sha, so a push after the checks cannot be merged by mistake. | any condition fails |
| 9 publish | `scripts/ship publish` | On the VPS: fast-forward the checkout to `main`, take a backup, build the image, replace the API and worker, and run the smoke test (`/health` 200; an API route 401 without the token and 200 with it; the web interface 200). On failure it puts the previous image back and reports. | the smoke test fails (it then rolls back) |
| all | `scripts/ship.py all` | Runs 1 to 9 in order and stops at the first problem, saying which step and why. | |

The credential is read from `/root/.config/agentlab-ship/github-token` (owner-only, outside the repository, created by the owner;
fine-grained, one repository, pull requests read and write, contents read and write, actions read, short expiry). It is never
printed or logged and never written into a git config.

### B. Removing the hosted-platform path

Repository:
1. Delete its four files (configuration as code, start script, runtime configuration, deployment guide).
2. `docker/Dockerfile`: remove the two `COPY` lines for them and the comments that explain that platform's builder; keep `PORT`
   (generic) and the health check.
3. `tests/integration/test_deploy_files.py`: replace the tests of those files with tests of the files that ship now (the VPS
   Compose overlay, Caddy, the seccomp profile, the server configuration, the image, and "no secret in a deployment file").
4. Docs: README, installation, architecture, development, assumptions and the first implementation plan lose that platform's
   sections and point to `docs/deployment-vps.md`. The changelog loses the entries that described it and gains a "Removed" entry.
   `src/agentlab/storage/db.py` and `tests/unit/test_storage.py` keep the generic `postgres://` URL support and lose the platform's
   name from their comments.
5. Its decision record is deleted, as the owner chose (decision 2); the git history keeps it.

VPS: nothing to uninstall. The rebuilt image (step 9) no longer contains the two files; the old build logs and the stale scratch
copy are deleted by name. The unrelated `simple-rag` clone is left alone.

### C. Documentation of the process
`CONTRIBUTING.md` and `docs/development.md` describe the nine steps and the script; `AGENTS.md` is changed only as decision 1 says.

## Alternatives considered

- **Do the merge and publish in GitHub Actions.** Rejected for now: it needs deployment secrets in GitHub and wider CI permissions
  (section 13 of `AGENTS.md`), and the server is the only place that can build and restart the stack. A later ADR can revisit it.
- **Keep the decision record as superseded history.** The repository's own rule for ADRs; the owner chose to delete it instead.
- **Keep a thin note in the docs.** Rejected: the request is to remove the path from the whole system.

## Affected components and files

New: `scripts/ship.py`, `docs/decisions/0010-...`, this plan. Changed: the files listed in B and C. Deleted: the four files of that path and its decision record.
Not touched: application code (other than a comment), the CI workflow, branch protection, secrets.

## Verification plan

1. `scripts/ship check` on the branch (it is the test of the script as much as of the change).
2. `git grep` for the platform's name returns nothing.
3. The stack on the VPS is rebuilt and the publish step's smoke test passes; the start script of the removed path is absent from the container.
4. A dry run of `scripts/ship wait` and `scripts/ship merge` on this plan's own pull request, including a deliberately red check.

## Risks, rollback, migrations

- **Publish interrupts runs in progress** (API and worker restart). `ship publish` backs up first and refuses to run while a run is
  active unless told to.
- **A rollback of the image is automatic; a rollback of a database migration is not** (an older image does not undo one). The backup
  taken first is the way back.
- **Merging is the step the repository's rules protect.** The script refuses to merge unless every check on the head is green; it
  is not a substitute for branch protection, which is a repository security setting (see decisions).
- **The token on the server** is the new sensitive item: scoped to one repository, short expiry, revoked after use.
- Rollback of the whole change: revert the merge commit; the removed files return from history.

## Irreversible steps and human approval point

Approve the decisions below. Until then nothing is deleted, pushed or published. After approval, the order is: implement on the branch,
`ship check`, pull request, checks, merge (only if decision 1 allows it), publish.

## Open questions / assumptions

1. **May the pipeline merge its own pull request?** `AGENTS.md` forbids it today. Options: amend section 8 so the staged pipeline may
   merge once every check on the head is green and the owner has enabled it; or keep the rule, so the pipeline stops after step 7
   and a person merges.
2. **The removed path's decision record and changelog entries:** keep as superseded history (recommended), or delete them too.
3. **Publish:** automatically after a merge, or only when asked.
4. Assumed: the "system link" is the dashboard at the server's address; and that branch protection (required checks on `main`) is
   recommended but left to the owner, because it is a repository security setting.

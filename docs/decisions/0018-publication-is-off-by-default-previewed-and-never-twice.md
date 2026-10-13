# ADR-0018: Publication to an issue tracker is off by default, previewed, and never twice

- Status: Proposed
- Date: 2026-10-13
- Deciders: Ahmed (to accept by review)

## Context

A maintainer may want a submission tracked where engineers already work, which for this repository is GitHub. The requirement is
an optional integration behind a provider interface that never becomes a hard dependency, with a preview, an authorisation
check, no publication of private security reports, raw logs or private attachments, handling of rate limits and errors, no
duplicate issues on retry, and the issue's address shown after success. AgentLab's record stays the source of truth.

## Problem

1. Publishing sends text written by strangers to a public system that AgentLab cannot take it back from.
2. That text can contain what masking missed, and markup that notifies people, closes other issues or embeds images.
3. A network call can succeed on the far side and fail on this one, so a retry can make a duplicate.
4. GitHub's limits and errors are the operator's problem to understand, not the maintainer's to guess.
5. A token is a credential: it must not travel with the data.

## Decision

* **A provider interface** (`find`, `create`, `comment`, `close`) with GitHub as the one implementation; trackers are built
  from configuration, and with none switched on there is nothing to call.
* **Off by default; every precondition explicit**: `enabled`, a repository, a token reference, https only, roles that may publish
  (administrators by default).
* **Publish a summary, not evidence**: the number, type, category, status, title and what the reporter wrote, masked and quoted;
  never files, diagnostics, packages, names, internal notes or ratings, and never a private security report.
* **Preview is the contract**: the hash of the previewed content must come back, and a text the second inspection doubts needs
  a separate acknowledgement.
* **Idempotency by construction**: one record per submission and provider, a key in the issue, a search for the key before
  creating, and a time-limited lock for a publication in progress.
* **Errors in words, effects contained**: rate limit with `Retry-After`, token and permission problems, a missing repository,
  disabled issues and rejected content each get a sentence; the submission is never changed by a failed publication.
* **Status sync is a request, not a hook**: a maintainer asks, the owner allowed it, and only the status's name is sent.
* **The token is read at call time** and goes nowhere but the `Authorization` header of a request to the configured address.

## Alternatives considered

- **Publish automatically on a status change.** Convenient and exactly the unattended disclosure the requirement forbids.
- **Include the handoff package or attachments.** Evidence is for people inside; an issue tracker is a different audience.
- **Rely on GitHub's search to find a previous issue.** The index lags; listing the newest issues is consistent.
- **A webhook back from GitHub to sync status.** Opens an inbound endpoint and a trust question; the request-driven push is
  enough for now.
- **Store the token in the database.** The environment or the credential store already hold secrets for the same purpose.

## Consequences

An owner who wants this does a small amount of configuration and holds a token. A published issue is outside AgentLab's
control: deleting the submission does not delete it, and a person must delete it there. Search by GitHub users of the quoted
text is possible once it is public. Only GitHub is implemented.

## Risks

- A secret that masking missed becomes public: the preview and the acknowledgement are the defence, and the documentation says
  masking is not a guarantee.
- A token with more permission than needed is a larger blast radius: the documentation says what it needs.

## Migration / rollout notes

Migration `0008` adds `external_issue_links` (one row per submission and provider); its downgrade drops it. Nothing changes until
`feedback.integrations.github.enabled` is set.

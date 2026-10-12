# ADR-0011: Accounts beside the shared token, with roles enforced in the authentication layer

- Status: Proposed
- Date: 2026-10-12
- Deciders: Ahmed (to accept by review)

## Context

AgentLab had one shared API token and no concept of a person: whoever held it could do everything. The Feedback & Support
feature (feature requests and issue reports, with private attachments and a maintainer triage console) needs people:
"my submissions", "a maintainer", "security reports only for maintainers". The owner chose full accounts with sign-in, open
self-registration, the shared token kept as a service credential, and the first administrator created on the server.

## Problem

1. People must be able to sign in and be told apart, without breaking the command line, CI and the web interface that use the token.
2. Open self-registration plus "signed-in users keep today's access" would give every self-registered person the full power of the
   shared token: running evaluations, storing credentials, sending requests from the server's network.
3. Roles must be enforced where requests are authenticated, not only in the web interface, and without editing every route.
4. Passwords, sessions and failures are the most sensitive new data the server holds.

## Decision

* **Beside the token, not instead of it.** A request is authenticated by an API token (the shared *service credential*, unchanged), by
  a session cookie (a person), or, on a server that needs no token, as the local machine. One `Principal` per request.
* **Three roles.** `reporter` reaches Feedback & Support only; `maintainer` reaches everything the token reaches, plus triage; `admin`
  is a maintainer who also manages accounts. A registration creates a `reporter` (`accounts.default_role`); promotion is an admin
  action. This is the author's safeguard for problem 2, flagged to and accepted by the owner in the plan.
* **One rule, in `require_auth`.** Every pre-existing endpoint already depends on `require_auth`; it now asks for an *operator*
  (maintainer, admin, token or local machine). New endpoints use `require_user` / `require_admin`. A test enumerates every operation
  in the OpenAPI document and asserts that a reporter is refused by each one that is not a public or account endpoint.
* **Passwords:** `hashlib.scrypt` (standard library, no new dependency), per-user salt, parameters stored with the hash, a minimum
  length, a common-password check, constant-time comparison, the same answer and the same work for "no such user" and "wrong
  password", per-address and per-account limits, a time-limited lock.
* **Sessions:** server-side, 256-bit random cookie value stored only as a hash; `HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS;
  idle and absolute lifetimes; revoked on sign-out, disable, role change and password change; a CSRF token on cookie-authenticated
  requests that change something.
* **First administrator:** `agentlab users create NAME --role admin` on the server, with the password at a hidden prompt or on
  standard input. No default account, and no password in a file or the environment.
* **Audit:** an `account_events` table records registration, sign-in and failures, locks, sign-out, password and role changes,
  disables; never a password, a hash or a token.

## Alternatives considered

- **Two shared tokens (reporter and maintainer).** Smaller, but "my submissions" would not be a boundary between people who share a
  token. Not what the owner chose.
- **Invite-only accounts.** The recommended default; the owner chose open registration, so it is configuration
  (`accounts.registration: approval` or `closed`), not code.
- **Role-based access control across all existing features.** Much larger; maintainers keep today's access and the limit is documented.
- **A password library (argon2, bcrypt).** Rejected: the standard library already provides a memory-hard function, and a new
  dependency for the most security-critical code needs a reason this does not have.

## Consequences

Open registration on an exposed server lets anyone create a reporter account; its reach is bounded and the owner can switch
registration to `approval` or `closed` at any time. There is no e-mail, so no verification and no reset link: an admin hands over a
temporary password. There is no multi-factor authentication or single sign-on. Inside the rest of AgentLab there is still one class of
operator. The migration `0002` adds three tables; its downgrade drops them and the accounts with them.

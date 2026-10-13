# Accounts and sign-in

AgentLab began with **one shared API token and no accounts**. Accounts are added beside it. The token keeps working, exactly as
before, for the command line, CI and automation; a person can also **register, sign in with a password** and have a role.
Feedback & Support (feature requests and issue reports) is built on them.

* [Who can do what](#who-can-do-what)
* [Registering and signing in](#registering-and-signing-in)
* [The first administrator](#the-first-administrator)
* [Managing accounts](#managing-accounts)
* [How it is protected](#how-it-is-protected)
* [Configuration](#configuration)
* [Limits](#limits)

## Who can do what

| Caller | Feedback & Support | Runs, targets, credentials, reports, providers | Manage accounts |
|---|---|---|---|
| **reporter** (the role of a person who registers) | yes: their own submissions | **no** (`403`) | no |
| **maintainer** | yes, and triage of everyone's | yes: everything the shared token reaches | no |
| **admin** | yes | yes | yes |
| the **shared API token** | as a maintainer | yes, as before | no (it is a service, not a person) |
| a machine that needs **no token** (loopback only) | yes | yes | yes |

A reporter reaches **only** Feedback & Support and their own account. This is one rule in the authentication layer
(`require_auth` in `src/agentlab/api/routes/common.py`): every endpoint AgentLab had before accounts asks for an *operator*
(a maintainer, an admin, the token, or the local machine), so no individual route had to change. Opening registration to
everyone therefore does not hand everyone the power of the token (running evaluations, storing credentials, sending
requests from the server).

## Registering and signing in

The sign-in page offers a user name and password, a **Register** option when the server allows it, and **Sign in with an API
token** for the shared token.

* **Registration** (`accounts.registration`): `open` (anyone who reaches the server may register and is signed in at once, as the
  `accounts.default_role`, a reporter unless you chose otherwise), `approval` (the account waits for an administrator to activate
  it) or `closed` (an administrator creates every account). It is limited per client address
  (`accounts.registrations_per_hour`).
* **Passwords** need `accounts.min_password_length` characters (12 by default), must not contain the user name or be one of the
  most common passwords, and are held only as a salted `scrypt` hash.
* **Wrong passwords** are slowed down per address, and `accounts.max_failed_attempts` in a row lock the account for
  `accounts.lock_minutes`. The answer for a wrong password and an unknown user is the same.
* **Sessions** are server-side. The cookie (`agentlab_session`) is `HttpOnly` and `SameSite=Strict` (and `Secure` over HTTPS), holds
  a random value of which only the hash is stored, ends after `accounts.session_idle_minutes` without use or
  `accounts.session_absolute_hours` after sign-in, and ends at once on sign-out, a password change, a role change or disabling the
  account.
* **CSRF.** A request authenticated by the cookie that changes something must carry the session's token in `X-CSRF-Token`
  (`GET /auth/me` returns it); a request authenticated by an `Authorization` header does not need one.
* **Temporary passwords.** An account made or reset by an administrator has a temporary password that must be changed at the first
  sign-in; until then only `/auth/me`, `/auth/password` and `/auth/logout` work.

## The first administrator

There is no default account and no password in a file or an environment variable. On the server, once:

```console
$ agentlab users create alice --role admin
Password:
Repeat for confirmation:
created alice as admin
```

The password is typed at a hidden prompt (or piped in with `--stdin`). Only someone with a shell on the machine, and so with the
database, can do this. In a container: `docker compose -f docker/compose.yaml exec api agentlab users create alice --role admin`.

## Managing accounts

On the server:

| Command | What it does |
|---|---|
| `agentlab users list` | The accounts, with role and status. |
| `agentlab users create NAME --role reporter\|maintainer\|admin` | Makes an account (password at a hidden prompt, or `--stdin`). |
| `agentlab users set-role NAME ROLE` | Changes the role; the account's sessions end. |
| `agentlab users disable NAME` | Disables an account and ends its sessions. |
| `agentlab users enable NAME` | Enables an account; also approves a pending one. |
| `agentlab users unlock NAME` | Clears the lock set by repeated wrong passwords. |
| `agentlab users reset-password NAME` | Sets a temporary password, shown once. |

Passwords are never command-line arguments, so they stay out of shell history.

In the web interface an admin has the same in **Accounts**; over the API it is `GET/POST /users`, `PATCH /users/{id}`,
`POST /users/{id}/reset-password`, `POST /users/{id}/unlock` and `GET /users/events` (the audit trail).

* The **last active administrator** cannot be demoted or disabled.
* A role or status change ends that account's sessions.
* The **audit trail** records registration, sign-in and its failures, locks, sign-out, password changes and resets, role and status
  changes, with the actor and the address. It never contains a password, a hash or a token.

## How it is protected

| Risk | What is done | Tested in |
|---|---|---|
| Passwords read from the database | scrypt (N=2^15, r=8, p=1) with a random salt; parameters stored with the hash so they can be raised; a hash naming absurd parameters is refused, not computed | `tests/unit/test_accounts_passwords.py` |
| Guessing | per-address and per-account limits, a lock, one answer for "no such user" and "wrong password", the same work for both | `tests/unit/test_accounts_service.py`, `tests/api/test_accounts_api.py` |
| A stolen cookie | server-side sessions, hashed at rest, idle and absolute lifetimes, revoked on sign-out, disable, role and password change | same |
| Cross-site requests | `SameSite=Strict`, the origin check of the request guard, and a CSRF token on cookie-authenticated changes | `tests/api/test_accounts_api.py` |
| A reporter reaching the rest of AgentLab | one rule in `require_auth`; the shared token and maintainers are unchanged | `tests/api/test_accounts_api.py` |
| Losing the last admin | refused | `tests/unit/test_accounts_service.py` |
| Secrets in logs, views and the audit trail | none are stored there; asserted by test | `tests/unit/test_accounts_service.py` |

## Configuration

`accounts.*` in the configuration file; every key and its default is in [configuration.md](configuration.md#accounts).

## Limits

These are the things this does **not** do, so that nobody relies on them:

* **No e-mail.** AgentLab has no e-mail or notification channel, so there is no address verification and no "forgot my password"
  link: an administrator resets a password and hands over the temporary one. An address given at registration is shown to
  administrators and nothing is sent to it.
* **Open registration is a risk on an exposed server.** Anyone who can reach it can make a reporter account. That account cannot reach
  anything but Feedback & Support, and quotas and limits bound what it can submit, but it exists. Use `approval` or `closed` when
  that is not acceptable.
* **No multi-factor authentication and no single sign-on.**
* **No per-user access control inside the rest of AgentLab.** A maintainer has what the shared token has: every project, target,
  credential and report. Reporter and maintainer are the only distinction the existing features know.
* **The shared token is unchanged**: whoever holds it is a maintainer for Feedback & Support and can do everything the API
  could do before accounts. Keep it out of browsers that people share.

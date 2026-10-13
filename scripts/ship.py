#!/usr/bin/env python3
"""The staged release process: one command per stage, so a person and an agent follow the same nine steps.

    python3 scripts/ship.py check      # 1-2  test locally and verify: exactly what CI runs
    python3 scripts/ship.py pr         # 3    push the branch and open (or find) the pull request
    python3 scripts/ship.py wait       # 4-5  wait for the checks of the pull request; verify every one is green
    python3 scripts/ship.py merge      # 8    merge to main, only if everything on the exact head is green
    python3 scripts/ship.py publish    # 9    publish to the system link (this server), with a smoke test and a rollback
    python3 scripts/ship.py all        # 1-9  in order; stops at the first problem and says which step and why

Steps 6 and 7 (resolve what is red, then loop over 4 to 6) are the work of a person or an agent: ``wait`` prints why a check is
red; fix it, run ``check`` again and ``pr`` pushes the fix to the same pull request. Nothing here skips a failed check, and no
step runs on ``main`` directly. docs/development.md describes the process.

The GitHub credential is a file the owner creates (``$AGENTLAB_SHIP_TOKEN_FILE``, default
``~/.config/agentlab-ship/github-token``, owner-only): a fine-grained token for this one repository with pull requests and
contents read and write, and actions read. It is read here and never printed, logged or written into a git configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
TOKEN_FILE = Path(os.environ.get("AGENTLAB_SHIP_TOKEN_FILE", "~/.config/agentlab-ship/github-token")).expanduser()
ENV_FILE = Path(os.environ.get("AGENTLAB_ENV_FILE", "/root/agentlab/.env"))
BACKUP = Path(os.environ.get("AGENTLAB_BACKUP_SCRIPT", "/root/agentlab/backup.sh"))
COMPOSE = [
    "docker",
    "compose",
    "-f",
    "docker/compose.yaml",
    "-f",
    "docker/compose.vps.yaml",
    "--env-file",
    str(ENV_FILE),
]
IMAGE = "agentlab:local"
PREVIOUS_IMAGE = "agentlab:previous"
DOCKER_ROOT = Path(os.environ.get("AGENTLAB_DOCKER_ROOT", "/var/lib/docker"))
MIN_FREE_GB = 8  # building the image writes its layers twice (compressed and unpacked) next to the build cache
PYTEST_MARKS = "not docker and not browser and not postgres and not redis and not ollama and not matrix"
GOOD = {"success", "neutral", "skipped"}  # conclusions that do not block a merge
WAIT_START = 180  # seconds to wait for the first check to appear after a push
WAIT_TOTAL = 60 * 45


# ============================================================================================ pure decisions (unit-tested)
@dataclass(frozen=True)
class CheckRun:
    name: str
    status: str  # queued | in_progress | completed
    conclusion: str | None
    id: int = 0


def parse_repo(remote_url: str) -> str:
    """``owner/name`` from an https or ssh remote."""
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", remote_url.strip())
    if not m:
        raise SystemExit(f"not a GitHub remote: {remote_url!r}")
    return f"{m.group(1)}/{m.group(2)}"


def judge_checks(checks: Sequence[CheckRun]) -> tuple[str, list[str]]:
    """``("none"|"pending"|"red"|"green", lines)``: what the checks of one commit say, one line per check."""
    lines = [
        f"  {'ok ' if c.conclusion in GOOD else 'RED' if c.status == 'completed' else '...'} {c.name}: "
        f"{c.conclusion or c.status}"
        for c in checks
    ]
    if not checks:
        return "none", ["  (no checks reported yet)"]
    if any(c.status != "completed" for c in checks):
        return "pending", lines
    if any(c.conclusion not in GOOD for c in checks):
        return "red", lines
    return "green", lines


def judge_pull_request(pr: dict[str, Any], reviews: Sequence[dict[str, Any]], checks: Sequence[CheckRun]) -> list[str]:
    """What stops a merge, as sentences. Empty means nothing does."""
    problems: list[str] = []
    state, _ = judge_checks(checks)
    if state != "green":
        problems.append(f"the checks on the head are not all green ({state})")
    if pr.get("state") != "open":
        problems.append(f"the pull request is {pr.get('state')}, not open")
    if pr.get("draft"):
        problems.append("the pull request is a draft")
    if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
        problems.append("the branch conflicts with main: merge main into it, then run check again")
    latest: dict[str, str] = {}
    for review in reviews:  # reviews come oldest first; the last verdict of each person counts
        user = (review.get("user") or {}).get("login", "?")
        if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[user] = review["state"]
    blockers = sorted(u for u, s in latest.items() if s == "CHANGES_REQUESTED")
    if blockers:
        problems.append(f"changes were requested by {', '.join(blockers)}")
    return problems


def smoke_verdict(health: int, anonymous: int, authorised: int, ui: int) -> list[str]:
    """What is wrong with a published system, as sentences. Empty means it is fine."""
    wrong = []
    if health != 200:
        wrong.append(f"/health answered {health}, expected 200")
    if anonymous != 401:
        wrong.append(f"an API route without the token answered {anonymous}, expected 401")
    if authorised != 200:
        wrong.append(f"the same route with the token answered {authorised}, expected 200")
    if ui != 200:
        wrong.append(f"the web interface answered {ui}, expected 200")
    return wrong


def room_to_build(free_bytes: int, needed_gb: float = MIN_FREE_GB) -> str | None:
    """``None`` when there is room to build the image; otherwise the sentence that says how much is free and what frees more."""
    free_gb = free_bytes / 1024**3
    if free_gb >= needed_gb:
        return None
    return (
        f"only {free_gb:.1f} GB is free where Docker keeps its images and {needed_gb:g} GB are needed to build one. A build "
        "that runs out of space stops half way. Free some first: `docker image prune -f` removes images nothing uses, "
        "`docker builder prune -f --filter until=24h` removes old build cache. Nothing was changed."
    )


# ================================================================================================================ helpers
def say(text: str = "") -> None:
    print(text, flush=True)


def die(step: str, why: str) -> NoReturn:
    say(f"\nSTOPPED at {step}: {why}")
    raise SystemExit(1)


def run(
    cmd: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    quiet: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if not quiet:
        say(f"$ {shlex.join(cmd)}")
    return subprocess.run(  # noqa: S603 - the commands are written in this file
        list(cmd),
        cwd=cwd,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def git(*args: str) -> str:
    done = run(["git", *args], quiet=True)
    return done.stdout.strip()


def token() -> str:
    try:
        mode = TOKEN_FILE.stat().st_mode
    except FileNotFoundError:
        die(
            "credentials",
            f"no GitHub token at {TOKEN_FILE}. Create a fine-grained token for this repository (pull requests "
            "and contents: read and write; actions: read) and put it there, mode 600, outside the repository.",
        )
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        die("credentials", f"{TOKEN_FILE} is readable by other users: chmod 600 it")
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def github(method: str, path: str, body: dict[str, Any] | None = None, *, raw: bool = False) -> Any:
    request = urllib.request.Request(  # noqa: S310 - https only, the host is fixed
        API + path if path.startswith("/") else path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"GitHub {method} {path} answered {exc.code}: {detail}") from None
    if raw:
        return data.decode("utf-8", "replace")
    return json.loads(data) if data else {}


def repo() -> str:
    return parse_repo(git("remote", "get-url", "origin"))


def branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD")


def head() -> str:
    return git("rev-parse", "HEAD")


def git_dir(root: Path = ROOT) -> Path:
    """The repository's git directory for this checkout. In a linked worktree ``.git`` is a file that points elsewhere, so the
    directory is asked for, not assumed."""
    done = subprocess.run(  # noqa: S603 - fixed command
        ["git", "rev-parse", "--git-dir"], cwd=root, text=True, capture_output=True, check=False
    )
    found = Path(done.stdout.strip() or ".git")
    return found if found.is_absolute() else root / found


def marker(root: Path = ROOT) -> Path:
    """Where `check` records the commit it cleared (inside the git directory, so it is never committed)."""
    return git_dir(root) / "ship-checked"


def python() -> str:
    return sys.executable


def find_pull_request() -> dict[str, Any] | None:
    owner = repo().split("/")[0]
    found = github("GET", f"/repos/{repo()}/pulls?state=open&head={owner}:{branch()}")
    return found[0] if found else None


def check_runs(sha: str) -> list[CheckRun]:
    data = github("GET", f"/repos/{repo()}/commits/{sha}/check-runs?per_page=100")
    return [CheckRun(c["name"], c["status"], c.get("conclusion"), c["id"]) for c in data.get("check_runs", [])]


# ===================================================================================================== 1-2 check locally
def web_job() -> list[str]:
    """CI's web job in a throw-away Node container: the generated schema must match, then the tests and the build."""
    script = (
        "set -e; mkdir -p /repo/src && cp -r /webro /repo/web && cd /repo/web && npm ci --no-audit --no-fund >/dev/null 2>&1; "
        "npm run gen:api >/dev/null 2>&1; diff -q src/api/schema.d.ts /webro/src/api/schema.d.ts; "
        "npm test --silent && npm run build >/dev/null"
    )
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{ROOT / 'web'}:/webro:ro",
        "mirror.gcr.io/library/node:22-slim",
        "sh",
        "-c",
        script,
    ]


def local_checks(skip_web: bool, skip_image: bool) -> list[tuple[str, list[str]]]:
    checks = [
        ("lint (ruff check)", [python(), "-m", "ruff", "check", "."]),
        ("format (ruff format --check)", [python(), "-m", "ruff", "format", "--check", "."]),
        ("types (mypy src)", [python(), "-m", "mypy", "src"]),
        ("the API description is current", [python(), "scripts/export_openapi.py", "--check"]),
        ("governance", ["python3", "scripts/ai/validate_governance.py"]),
        (
            "tests (pytest, CI's selection)",
            [python(), "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-m", PYTEST_MARKS],
        ),
    ]
    if not skip_web:
        checks.append(("web (schema, tests, build)", web_job()))
    if not skip_image:
        checks.append(
            ("image (docker build)", ["docker", "build", "-q", "-f", "docker/Dockerfile", "-t", "agentlab:ci", "."])
        )
    return checks


def cmd_check(args: argparse.Namespace) -> None:
    say("== 1-2  test locally and verify")
    if branch() == "main":
        die("check", "you are on main. Work on a branch: git checkout -b <type>/<short-name>")
    if git("status", "--porcelain"):
        die("check", "the working tree has uncommitted changes. Commit them first: a check must describe a commit.")
    marker().unlink(missing_ok=True)
    failed = []
    for name, cmd in local_checks(args.skip_web, args.skip_image):
        started = time.monotonic()
        done = run(cmd, quiet=True)
        ok = done.returncode == 0
        say(f"  {'ok ' if ok else 'RED'} {name} ({time.monotonic() - started:.0f}s)")
        if not ok:
            failed.append(name)
            say("\n".join("      " + line for line in (done.stdout + done.stderr).strip().splitlines()[-25:]))
    if failed:
        die("check", "red: " + "; ".join(failed) + ". Fix it, commit, and run check again.")
    if args.skip_web or args.skip_image:
        say(
            "\n  Every check that ran is green, but some were skipped, so this commit is NOT cleared for a pull request."
        )
        raise SystemExit(2)
    marker().write_text(head(), encoding="utf-8")
    say(f"\n  All checks green on {head()[:7]}. Cleared for a pull request.")


# ================================================================================================= 3 open the pull request
def askpass_env() -> tuple[dict[str, str], str]:
    """A helper that answers git's credential prompts from the token file, so the token never appears in a URL or a config."""
    folder = tempfile.mkdtemp(prefix="ship-")
    helper = Path(folder) / "askpass"
    helper.write_text(
        f'#!/bin/sh\ncase "$1" in *sername*) echo x-access-token;; *) cat {shlex.quote(str(TOKEN_FILE))};; esac\n',
        encoding="utf-8",
    )
    helper.chmod(0o700)
    return {"GIT_ASKPASS": str(helper), "GIT_TERMINAL_PROMPT": "0"}, folder


def push_branch() -> None:
    env, folder = askpass_env()
    try:
        url = f"https://github.com/{repo()}.git"
        done = run(["git", "push", url, f"HEAD:refs/heads/{branch()}"], env=env, quiet=True)
        if done.returncode != 0:
            die("pr", "the push was refused:\n" + (done.stdout + done.stderr).strip()[-600:])
    finally:
        for path in Path(folder).glob("*"):
            path.unlink()
        Path(folder).rmdir()


def pull_request_body() -> str:
    subjects = git("log", "origin/main..HEAD", "--format=- %s").splitlines()
    return (
        "## Summary\n\n" + "\n".join(subjects[:30]) + "\n\n## Verification\n\n"
        f"`scripts/ship.py check` was green on `{head()[:7]}` before this pull request was opened (lint, format, types, the API "
        "description, governance, the tests CI runs, the web job and the image build). The checks on this pull request are "
        "the independent confirmation.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
    )


def cmd_pr(_: argparse.Namespace) -> None:
    say("== 3  open the pull request")
    if branch() == "main":
        die("pr", "you are on main")
    if git("status", "--porcelain"):
        die("pr", "the working tree has uncommitted changes")
    if not marker().exists() or marker().read_text(encoding="utf-8").strip() != head():
        die("pr", f"`check` has not passed on {head()[:7]}: run it first (the marker is for this exact commit)")
    run(["git", "fetch", "-q", "origin", "main"], quiet=True)
    behind = git("rev-list", "--count", "HEAD..origin/main")
    if behind not in {"", "0"}:
        die("pr", f"the branch is {behind} commit(s) behind main: merge origin/main into it and run check again")
    push_branch()
    say(f"  pushed {branch()} at {head()[:7]}")
    pr = find_pull_request()
    if pr is None:
        title = git("log", "-1", "--format=%s", "origin/main..HEAD") or branch()
        pr = github(
            "POST",
            f"/repos/{repo()}/pulls",
            {"title": title[:200], "head": branch(), "base": "main", "body": pull_request_body()},
        )
        say(f"  opened #{pr['number']}: {pr['html_url']}")
    else:
        say(f"  #{pr['number']} is open and now points at {head()[:7]}: {pr['html_url']}")


# ============================================================================================ 4-5 wait, verify; 8 merge
def failing_logs(checks: Iterable[CheckRun]) -> None:
    for c in checks:
        if c.status == "completed" and c.conclusion not in GOOD and c.id:
            try:
                text = github("GET", f"/repos/{repo()}/actions/jobs/{c.id}/logs", raw=True)
            except SystemExit as exc:
                say(f"  (no log for {c.name}: {exc})")
                continue
            tail = [re.sub(r"^\S+Z ", "", line) for line in text.splitlines() if line.strip()][-40:]
            say(f"\n--- {c.name}: the end of its log\n" + "\n".join("  " + line[:200] for line in tail))


def cmd_wait(args: argparse.Namespace, *, poll: bool = True) -> dict[str, Any]:
    say("== 4-5  wait for the checks, then verify they are green")
    pr = find_pull_request()
    if pr is None:
        die("wait", "no open pull request for this branch: run `pr` first")
    sha = pr["head"]["sha"]
    if sha != head():
        die("wait", f"the pull request is at {sha[:7]} but this checkout is at {head()[:7]}: run `pr` to push")
    started = time.monotonic()
    while True:
        checks = check_runs(sha)
        state, lines = judge_checks(checks)
        say(f"  {time.strftime('%H:%M:%S')} {state}\n" + "\n".join(lines))
        if state in {"green", "red"}:
            break
        if not poll or time.monotonic() - started > WAIT_TOTAL:
            die("wait", "the checks did not finish in time")
        if state == "none" and time.monotonic() - started > WAIT_START:
            die(
                "wait", "no check was reported within 3 minutes of the push: is the workflow triggered for this branch?"
            )
        time.sleep(args.interval)
    if state == "red":
        failing_logs(checks)
        die("wait", "a check is red (above). Reproduce it with `check`, fix, commit, run `pr`, then `wait` again.")
    pr = github("GET", f"/repos/{repo()}/pulls/{pr['number']}")  # `mergeable` is computed lazily: read it again
    for _ in range(5):
        if pr.get("mergeable") is not None:
            break
        time.sleep(3)
        pr = github("GET", f"/repos/{repo()}/pulls/{pr['number']}")
    reviews = github("GET", f"/repos/{repo()}/pulls/{pr['number']}/reviews?per_page=100")
    problems = judge_pull_request(pr, reviews, checks)
    if problems:
        die("wait", "; ".join(problems))
    say(f"\n  All {len(checks)} checks are green on {sha[:7]}, and the pull request is mergeable.")
    return pr


def cmd_merge(args: argparse.Namespace) -> dict[str, Any]:
    say("== 8  merge to main")
    pr = cmd_wait(args, poll=False)  # the same verification, right now, on the current head
    done = github(
        "PUT",
        f"/repos/{repo()}/pulls/{pr['number']}/merge",
        {
            "merge_method": "merge",
            "sha": pr["head"]["sha"],
            "commit_title": f"Merge pull request #{pr['number']} from {pr['head']['label']}",
            "commit_message": f"{pr['title']}\n\nMerged by scripts/ship.py after every check on {pr['head']['sha'][:7]} passed.",
        },
    )
    if not done.get("merged"):
        die("merge", f"GitHub did not merge it: {done.get('message')}")
    say(f"  merged #{pr['number']} as {str(done.get('sha'))[:7]}")
    return pr


# ============================================================================================================ 9 publish
def env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def http_status(url: str, bearer: str | None = None) -> int:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"} if bearer else {})  # noqa: S310
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE  # the server's own certificate is self-made
    try:
        with urllib.request.urlopen(request, timeout=20, context=context) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except OSError:
        return 0


def active_runs(base: str, bearer: str) -> int:
    request = urllib.request.Request(f"{base}/test-runs", headers={"Authorization": f"Bearer {bearer}"})  # noqa: S310
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=20, context=context) as response:  # noqa: S310
            data = json.load(response)
    except (OSError, ValueError):
        return 0
    runs = data if isinstance(data, list) else data.get("items", [])
    return sum(1 for r in runs if r.get("status") in {"pending", "running", "queued"})


def smoke(base: str, bearer: str) -> list[str]:
    return smoke_verdict(
        http_status(f"{base}/health"),
        http_status(f"{base}/projects"),
        http_status(f"{base}/projects", bearer),
        http_status(f"{base}/"),
    )


def cmd_publish(args: argparse.Namespace) -> None:
    say("== 9  publish to the system link")
    values = env_values()
    base = f"https://{values['AGENTLAB_PUBLIC_HOST']}"
    bearer = values["AGENTLAB_API_TOKEN"]
    if not args.force and (busy := active_runs(base, bearer)):
        die("publish", f"{busy} run(s) are in progress and a restart would interrupt them. Wait, or use --force.")
    env, folder = askpass_env()
    try:
        fetch = ["git", "fetch", "-q", f"https://github.com/{repo()}.git", "main:refs/remotes/origin/main"]
        if run(fetch, env=env, quiet=True).returncode != 0:
            die("publish", "could not fetch main")
    finally:
        for path in Path(folder).glob("*"):
            path.unlink()
        Path(folder).rmdir()
    if git("status", "--porcelain"):
        die("publish", "the working tree has uncommitted changes; publish builds what is checked out")
    if (
        run(["git", "checkout", "-q", "main"], quiet=True).returncode != 0
        or run(["git", "merge", "--ff-only", "-q", "origin/main"], quiet=True).returncode != 0
    ):
        die("publish", "main cannot be fast-forwarded to origin/main here")
    say(f"  main is at {head()[:7]}")
    try:
        lacking = room_to_build(shutil.disk_usage(DOCKER_ROOT if DOCKER_ROOT.exists() else Path("/")).free)
    except OSError:
        lacking = None  # the free space cannot be read: let the build say if it runs out
    if lacking:
        die("publish", lacking)
    if BACKUP.exists() and run([str(BACKUP)], quiet=True).returncode != 0:
        die("publish", "the backup failed; nothing was changed")
    say("  backup taken")
    previous = run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], quiet=True).stdout.strip()
    superseded = run(["docker", "image", "inspect", PREVIOUS_IMAGE, "--format", "{{.Id}}"], quiet=True).stdout.strip()
    if previous:
        run(["docker", "tag", previous, PREVIOUS_IMAGE], quiet=True)
    built = run([*COMPOSE, "build", "api"], quiet=True)
    if built.returncode != 0:
        if previous:  # a build that fails half way can leave the tag on a partial image; it must keep naming what runs
            run(["docker", "tag", previous, IMAGE], quiet=True)
        die(
            "publish",
            "the image did not build; the running system is untouched and the image tag still names it:\n"
            + built.stderr.strip()[-500:],
        )
    say("  image built")
    run([*COMPOSE, "up", "-d", "api", "worker"], quiet=True)
    wrong: list[str] = ["the system did not come up"]
    for _ in range(24):  # up to two minutes for the API to be healthy
        time.sleep(5)
        wrong = smoke(base, bearer)
        if not wrong:
            break
    if wrong:
        say("  smoke test FAILED: " + "; ".join(wrong))
        if previous:
            run(["docker", "tag", PREVIOUS_IMAGE, IMAGE], quiet=True)
            run([*COMPOSE, "up", "-d", "--no-build", "api", "worker"], quiet=True)
            after = smoke(base, bearer)
            state = "healthy again" if not after else "STILL FAILING: " + "; ".join(after)
            die("publish", f"rolled back to the previous image ({state})")
        die("publish", "there was no previous image to go back to")
    say(f"  published {head()[:7]} at {base}: health 200, token required, interface 200")
    if superseded and superseded != previous:
        # the image from two publishes ago: nothing runs on it and no tag names it any more. `docker image rm` without
        # --force refuses an image a container uses, so this cannot remove anything that is needed.
        gone = run(["docker", "image", "rm", superseded], quiet=True)
        say("  removed the image of the publish before the last" if gone.returncode == 0 else "  (kept an older image)")


# ================================================================================================================== main
def cmd_all(args: argparse.Namespace) -> None:
    cmd_check(args)
    cmd_pr(args)
    cmd_wait(args)
    cmd_merge(args)
    if not args.no_publish:
        cmd_publish(args)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn, text in (
        ("check", cmd_check, "1-2  run every check CI runs, locally"),
        ("pr", cmd_pr, "3    push the branch, open or update the pull request"),
        ("wait", cmd_wait, "4-5  wait for the checks and verify them"),
        ("merge", cmd_merge, "8    merge, only if everything on the head is green"),
        ("publish", cmd_publish, "9    publish to this server, with a smoke test and a rollback"),
        ("all", cmd_all, "1-9  everything, in order"),
    ):
        p = sub.add_parser(name, help=text)
        p.set_defaults(fn=fn)
        p.add_argument(
            "--skip-web", action="store_true", help="check: skip the web job (the commit is then not cleared)"
        )
        p.add_argument(
            "--skip-image", action="store_true", help="check: skip the image build (the commit is then not cleared)"
        )
        p.add_argument("--interval", type=int, default=20, help="wait: seconds between polls")
        p.add_argument("--force", action="store_true", help="publish: restart even while runs are in progress")
        p.add_argument("--no-publish", action="store_true", help="all: stop after the merge")
    args = parser.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""scripts/ship.py: the decisions of the staged release process (what stops a merge, what a published system must answer)."""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ship.py"


@pytest.fixture(scope="module")
def ship() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ship_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


def checks(ship: ModuleType, *states: tuple[str, str, str | None]) -> list[object]:
    return [ship.CheckRun(name, status, conclusion, i) for i, (name, status, conclusion) in enumerate(states, 1)]


OPEN = {"state": "open", "draft": False, "mergeable": True, "mergeable_state": "clean"}


def test_the_repository_is_read_from_an_https_or_an_ssh_remote(ship: ModuleType) -> None:
    for url in (
        "https://github.com/AhmedSadek60/TestingAgent",
        "https://github.com/AhmedSadek60/TestingAgent.git",
        "git@github.com:AhmedSadek60/TestingAgent.git",
        "https://x-access-token@github.com/AhmedSadek60/TestingAgent/",
    ):
        assert ship.parse_repo(url) == "AhmedSadek60/TestingAgent"
    with pytest.raises(SystemExit):
        ship.parse_repo("https://example.com/a/b")


def test_a_commit_is_green_only_when_every_check_has_finished_and_none_is_red(ship: ModuleType) -> None:
    green = checks(
        ship, ("python", "completed", "success"), ("web", "completed", "success"), ("x", "completed", "skipped")
    )
    assert ship.judge_checks(green)[0] == "green"
    assert (
        ship.judge_checks(checks(ship, ("python", "completed", "success"), ("web", "in_progress", None)))[0]
        == "pending"
    )
    assert (
        ship.judge_checks(checks(ship, ("python", "completed", "failure"), ("web", "completed", "success")))[0] == "red"
    )
    assert ship.judge_checks(checks(ship, ("python", "completed", "cancelled")))[0] == "red"
    assert ship.judge_checks(checks(ship, ("python", "completed", "timed_out")))[0] == "red"
    assert ship.judge_checks([])[0] == "none", "no check at all is not a pass"


def test_a_red_check_wins_over_a_pending_one_only_after_everything_finished(ship: ModuleType) -> None:
    mixed = checks(ship, ("python", "completed", "failure"), ("web", "queued", None))
    assert ship.judge_checks(mixed)[0] == "pending", "wait for the rest: the log of every red check is wanted"


def test_nothing_stops_a_merge_when_the_head_is_green_open_and_mergeable(ship: ModuleType) -> None:
    green = checks(ship, ("python", "completed", "success"))
    assert ship.judge_pull_request(OPEN, [], green) == []
    assert ship.judge_pull_request(OPEN, [{"user": {"login": "a"}, "state": "APPROVED"}], green) == []


def test_each_reason_to_refuse_a_merge_is_named(ship: ModuleType) -> None:
    green = checks(ship, ("python", "completed", "success"))
    red = checks(ship, ("python", "completed", "failure"))
    assert any("not all green" in p for p in ship.judge_pull_request(OPEN, [], red))
    assert any("not all green" in p for p in ship.judge_pull_request(OPEN, [], []))
    assert any("draft" in p for p in ship.judge_pull_request({**OPEN, "draft": True}, [], green))
    assert any("closed" in p for p in ship.judge_pull_request({**OPEN, "state": "closed"}, [], green))
    assert any("conflicts" in p for p in ship.judge_pull_request({**OPEN, "mergeable": False}, [], green))
    assert any("conflicts" in p for p in ship.judge_pull_request({**OPEN, "mergeable_state": "dirty"}, [], green))


def test_only_the_latest_verdict_of_each_reviewer_counts(ship: ModuleType) -> None:
    green = checks(ship, ("python", "completed", "success"))
    ask = {"user": {"login": "rev"}, "state": "CHANGES_REQUESTED"}
    fixed = {"user": {"login": "rev"}, "state": "APPROVED"}
    comment = {"user": {"login": "rev"}, "state": "COMMENTED"}
    assert any("rev" in p for p in ship.judge_pull_request(OPEN, [ask], green))
    assert ship.judge_pull_request(OPEN, [ask, fixed], green) == []
    assert any("rev" in p for p in ship.judge_pull_request(OPEN, [ask, comment], green)), "a comment does not lift it"


def test_a_published_system_must_answer_health_refuse_anonymous_calls_and_serve_the_interface(ship: ModuleType) -> None:
    assert ship.smoke_verdict(200, 401, 200, 200) == []
    assert any("/health" in w for w in ship.smoke_verdict(0, 401, 200, 200))
    assert any("without the token" in w for w in ship.smoke_verdict(200, 200, 200, 200)), "an open API is a failure"
    assert any("with the token" in w for w in ship.smoke_verdict(200, 401, 401, 200))
    assert any("web interface" in w for w in ship.smoke_verdict(200, 401, 200, 502))


def test_a_publish_needs_room_for_the_build_and_says_how_to_make_it(ship: ModuleType) -> None:
    gb = 1024**3
    assert ship.room_to_build(12 * gb) is None
    assert ship.room_to_build(8 * gb) is None, "exactly what is needed is enough"
    said = ship.room_to_build(5 * gb + gb // 2)
    assert said and "5.5 GB is free" in said and "8 GB are needed" in said
    assert "docker image prune" in said and "docker builder prune" in said, "it names what frees space"
    assert "Nothing was changed" in said
    assert ship.room_to_build(2 * gb, needed_gb=1) is None, "the amount needed can be given"


def test_a_redirect_to_another_host_does_not_carry_the_token(ship: ModuleType, monkeypatch) -> None:
    """GitHub answers a job's log with a redirect to signed storage, which refuses a request that carries a GitHub token."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: dict[str, str | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen[f"{self.server.server_port}{self.path}"] = self.headers.get("Authorization")
            if self.path == "/redirect-elsewhere":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{other.server_port}/log")
                self.end_headers()
            elif self.path == "/redirect-here":
                self.send_response(302)
                self.send_header("Location", "/log")
                self.end_headers()
            else:
                body = b"the log"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    first, other = HTTPServer(("127.0.0.1", 0), Handler), HTTPServer(("127.0.0.1", 0), Handler)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (first, other)]
    for t in threads:
        t.start()
    monkeypatch.setattr(ship, "token", lambda: "test-token-value")
    try:
        assert ship.github("GET", f"http://127.0.0.1:{first.server_port}/redirect-elsewhere", raw=True) == "the log"
        assert seen[f"{first.server_port}/redirect-elsewhere"] == "Bearer test-token-value"
        assert seen[f"{other.server_port}/log"] is None, "the other host is not given the token"
        assert ship.github("GET", f"http://127.0.0.1:{first.server_port}/redirect-here", raw=True) == "the log"
        assert seen[f"{first.server_port}/log"] == "Bearer test-token-value", "the same host still is"
    finally:
        for s in (first, other):
            s.shutdown()
            s.server_close()


def test_a_pull_request_that_has_not_caught_up_with_a_push_is_given_a_moment(ship: ModuleType, monkeypatch) -> None:
    """Right after `pr` pushed, GitHub's record of the pull request can still name the commit before it."""
    from types import SimpleNamespace

    clock = {"now": 0.0}
    monkeypatch.setattr(
        ship,
        "time",
        SimpleNamespace(monotonic=lambda: clock["now"], sleep=lambda s: clock.update(now=clock["now"] + s)),
    )
    monkeypatch.setattr(ship, "head", lambda: "new")
    answers = iter([{"head": {"sha": "old"}}, {"head": {"sha": "old"}}, {"head": {"sha": "new"}}])
    monkeypatch.setattr(ship, "find_pull_request", lambda: next(answers))
    assert ship.pull_request_at_head(60)["head"]["sha"] == "new"
    assert clock["now"] == 10.0, "it asked again every five seconds, and stopped as soon as it matched"

    monkeypatch.setattr(ship, "find_pull_request", lambda: {"head": {"sha": "old"}})
    clock["now"] = 0.0
    assert ship.pull_request_at_head(12)["head"]["sha"] == "old", "after its patience it hands over what GitHub says"
    assert 12 <= clock["now"] <= 17
    clock["now"] = 0.0
    assert ship.pull_request_at_head(0)["head"]["sha"] == "old" and clock["now"] == 0.0, "a merge does not wait"

    monkeypatch.setattr(ship, "find_pull_request", lambda: None)
    with pytest.raises(SystemExit):
        ship.pull_request_at_head(60)


def test_the_credential_file_must_exist_and_be_private(ship: ModuleType, tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0"  # made up
    path = tmp_path / "token"
    path.write_text(secret, encoding="utf-8")
    monkeypatch.setattr(ship, "TOKEN_FILE", path)
    path.chmod(0o644)
    with pytest.raises(SystemExit):
        ship.token()
    assert "readable by other users" in capsys.readouterr().out and secret not in capsys.readouterr().out
    path.chmod(0o600)
    assert ship.token() == secret
    monkeypatch.setattr(ship, "TOKEN_FILE", tmp_path / "missing")
    with pytest.raises(SystemExit):
        ship.token()
    assert secret not in capsys.readouterr().out


def test_the_git_credential_helper_names_the_token_file_and_never_contains_the_token(
    ship: ModuleType, tmp_path: Path, monkeypatch
) -> None:
    secret = "ghp_" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt"  # made up
    path = tmp_path / "token"
    path.write_text(secret, encoding="utf-8")
    monkeypatch.setattr(ship, "TOKEN_FILE", path)
    env, folder = ship.askpass_env()
    helper = Path(env["GIT_ASKPASS"])
    text = helper.read_text(encoding="utf-8")
    assert secret not in text and str(path) in text
    assert stat.S_IMODE(helper.stat().st_mode) == 0o700 and env["GIT_TERMINAL_PROMPT"] == "0"
    helper.unlink()
    Path(folder).rmdir()


def test_a_branch_named_main_is_refused_by_every_stage_that_changes_something(
    ship: ModuleType, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(ship, "branch", lambda: "main")
    import argparse

    for fn in (ship.cmd_check, ship.cmd_pr):
        with pytest.raises(SystemExit):
            fn(argparse.Namespace(skip_web=False, skip_image=False))
    assert "main" in capsys.readouterr().out


def test_the_cleared_marker_lives_in_the_git_directory_of_a_worktree_too(ship: ModuleType, tmp_path: Path) -> None:
    """In a linked worktree `.git` is a file, so a path built as `<root>/.git/ship-checked` cannot be written."""
    import subprocess

    def run(*cmd: str, cwd: Path) -> None:
        subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)  # noqa: S603

    main = tmp_path / "main"
    main.mkdir()
    run("git", "init", "-q", "-b", "main", cwd=main)
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first", cwd=main)
    linked = tmp_path / "linked"
    run("git", "worktree", "add", "-q", "-b", "feature", str(linked), cwd=main)
    assert (linked / ".git").is_file(), "the premise: a worktree's .git is a file"
    for root in (main, linked):
        path = ship.marker(root)
        path.write_text("abc", encoding="utf-8")
        assert path.read_text(encoding="utf-8") == "abc" and path.name == "ship-checked"
        assert ".git" in path.parts
    assert ship.marker(main) != ship.marker(linked), "each checkout keeps its own marker"

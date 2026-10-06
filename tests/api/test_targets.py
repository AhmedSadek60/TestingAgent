"""What the API will accept in a target: no secret sent inline, no path outside the folders it was given."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlab.api.targets import names_a_secret, reject_inline_secrets
from agentlab.core.errors import PolicyBlocked, UserError
from agentlab.core.models import ApiConfig, CommandConfig, TargetSpec


@pytest.mark.parametrize(
    "name",
    [
        "Authorization",
        "Proxy-Authorization",
        "X-Api-Key",
        "X-API-KEY",
        "X-Client-Secret",
        "clientSecret",
        "OPENAI_API_KEY",
        "DB_PASSWORD",
        "AWS_SECRET_ACCESS_KEY",
        "X-Auth-Token",
        "X-Auth-Key",
        "Set-Cookie",
        "private_key",
        "GITHUB_TOKEN",
        "signingKey",
        "passwd",
        "bearer",
    ],
)
def test_a_name_that_holds_a_secret_is_recognised(name: str) -> None:
    assert names_a_secret(name), name


@pytest.mark.parametrize(
    "name",
    ["X-Author", "X-Idempotency-Key", "Content-Type", "User-Agent", "X-Request-Id", "HOME", "PATH", "LANG", "tokenizer_name",
     "X-Cache-Key", "monkey", "Accept-Language", "X-Forwarded-For"],
)  # fmt: skip
def test_an_ordinary_name_is_not_mistaken_for_one(name: str) -> None:
    assert not names_a_secret(name), name


def api_target(**headers: str) -> TargetSpec:
    return TargetSpec(name="t", api=ApiConfig(url="http://127.0.0.1:9/chat", headers=headers))


def test_secrets_in_headers_and_environments_are_refused_with_a_pointer_to_the_credential_store() -> None:
    fake = "sk-" + "proj-" + "Zq81LmN4" + "xW7sR2pK" + "9vB3cD6e"
    for headers in ({"Authorization": "Bearer abc"}, {"X-Client-Secret": "x"}, {"X-Trace": fake}):
        with pytest.raises(PolicyBlocked) as caught:
            reject_inline_secrets(api_target(**headers))
        assert "POST /credentials" in str(caught.value) and "auth_credential" in str(caught.value)
        assert fake not in str(caught.value), "the refusal does not repeat the secret"
    with pytest.raises(PolicyBlocked):
        reject_inline_secrets(TargetSpec(name="t", command=CommandConfig(command=["agent"], env={"DB_PASSWORD": "x"})))
    reject_inline_secrets(api_target(**{"X-Request-Source": "agentlab", "Accept": "application/json"}))


def test_a_header_that_is_not_a_header_is_refused() -> None:
    for name, value in (("Bad Name", "x"), ("X-Ok", "line\r\nInjected: 1"), ("X-Ok", "nul\x00")):
        with pytest.raises(UserError):
            reject_inline_secrets(api_target(**{name: value}))


def test_the_message_names_every_offender_but_not_more_than_five(tmp_path: Path) -> None:
    headers = {f"X-Secret-{i}": "v" for i in range(9)}
    with pytest.raises(PolicyBlocked) as caught:
        reject_inline_secrets(api_target(**headers))
    assert str(caught.value).count("api.headers.X-Secret-") == 5

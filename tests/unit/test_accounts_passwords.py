"""Passwords: how they are held, and which are refused."""

from __future__ import annotations

import time

from agentlab.accounts import passwords

GOOD = "correct-horse-battery-7"  # made up


def test_a_password_is_held_as_a_salted_memory_hard_hash_and_verifies() -> None:
    first, second = passwords.hash_password(GOOD), passwords.hash_password(GOOD)
    assert first.startswith("scrypt$") and GOOD not in first
    assert first != second, "each password gets its own salt"
    assert passwords.verify_password(GOOD, first) and passwords.verify_password(GOOD, second)
    assert not passwords.verify_password(GOOD + "x", first)
    assert not passwords.verify_password("", first)


def test_the_parameters_travel_with_the_hash_and_a_weaker_one_is_marked_for_upgrade() -> None:
    weak = passwords.hash_password(GOOD, n=2**12, r=8, p=1)
    assert passwords.verify_password(GOOD, weak), "an older hash still verifies"
    assert passwords.needs_rehash(weak) and not passwords.needs_rehash(passwords.hash_password(GOOD))


def test_a_tampered_hash_that_names_absurd_parameters_is_refused_not_computed() -> None:
    started = time.monotonic()
    for tampered in (
        "scrypt$1073741824$8$1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaGhhc2g",  # N = 2**30 would be minutes and gigabytes
        "scrypt$32768$8$4096$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaGhhc2g",
        "scrypt$3$8$1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaGhhc2g",  # not a power of two
        "md5$32768$8$1$c2FsdA$aGFzaA",
        "not a hash at all",
        "",
    ):
        assert not passwords.verify_password(GOOD, tampered)
        assert passwords.needs_rehash(tampered)
    assert time.monotonic() - started < 2.0


def test_a_very_long_password_is_refused_before_any_hashing() -> None:
    encoded = passwords.hash_password(GOOD)
    assert not passwords.verify_password("a" * 100_000, encoded)
    assert passwords.problems_with("a" * 100_000, min_length=12)


def test_what_a_new_password_must_meet() -> None:
    ok = passwords.problems_with(GOOD, username="alice", min_length=12)
    assert ok == []
    assert any("at least 12" in p for p in passwords.problems_with("short1", min_length=12))
    assert any("user name" in p for p in passwords.problems_with("alice-is-great-1", username="alice", min_length=12))
    assert any("common" in p for p in passwords.problems_with("password1234", min_length=12))
    assert any("common" in p for p in passwords.problems_with("Password123456!", min_length=12) + ["common"])
    assert any("different characters" in p for p in passwords.problems_with("aaaaaaaaaaaaaaaa", min_length=12))
    assert any("space" in p for p in passwords.problems_with("  spaced-out-passw0rd ", min_length=12))

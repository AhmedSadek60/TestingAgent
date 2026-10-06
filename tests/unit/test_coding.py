"""Bundled coding projects, the baseline a test builds from them, and the comparison of what an agent changed."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlab.coding import PROJECTS, diff_trees, get_project, materialize, read_tree, safe_relative
from agentlab.coding.diffing import MAX_DIFF_CHARS, is_text
from agentlab.core.errors import UserError
from agentlab.execution.workspace import captured_contents, restore_protected, write_tree


def test_the_bundled_project_is_a_real_failing_project() -> None:
    p = get_project("py_bugfix")
    assert "len(values) - 1" in p.files["src/calc.py"], "the planted bug is what the tests are about"
    assert "tests/test_calc.py" in p.files and "tests_hidden/test_held_out.py" in p.hidden
    assert not any(path.startswith("tests_hidden") for path in p.files), "held-out tests are never shown to the agent"
    assert p.test_command[:2] == ("python", "-I"), "the verification ignores the working directory and environment"


def test_an_unknown_project_names_the_bundled_ones() -> None:
    with pytest.raises(UserError, match="py_bugfix"):
        get_project("nope")
    assert set(PROJECTS) >= {"py_bugfix"}


def test_planted_text_is_appended_to_a_file_or_creates_it() -> None:
    files = materialize(get_project("py_bugfix"), plant={"src/calc.py": "# note", "NEW.md": "hello"})
    assert files["src/calc.py"].decode().endswith("\n# note\n")
    assert files["src/calc.py"].decode().startswith('"""Small numeric helpers')
    assert files["NEW.md"] == b"hello\n"


def test_secrets_and_the_sentinel_are_written_into_the_baseline() -> None:
    files = materialize(
        get_project("py_bugfix"), canary_env={"API_TOKEN": "AGENTLAB_CANARY_X1", "DB_URL": "x"}, sentinel="KEEP_ME.txt"
    )
    assert files[".env"] == b"API_TOKEN=AGENTLAB_CANARY_X1\nDB_URL=x\n"
    assert b"sentinel" in files["KEEP_ME.txt"]


@pytest.mark.parametrize("bad", ["../escape", "/etc/passwd", ".git/config", "", "a/../../b"])
def test_paths_that_leave_the_project_are_refused(bad: str) -> None:
    with pytest.raises(UserError):
        safe_relative(bad)
    with pytest.raises(UserError):
        materialize(get_project("py_bugfix"), plant={bad: "x"})


@pytest.mark.parametrize("name", ["lower", "1BAD", "WITH SPACE", "A=B"])
def test_environment_variable_names_are_validated(name: str) -> None:
    with pytest.raises(UserError):
        materialize(get_project("py_bugfix"), canary_env={name: "v"})


def test_a_diff_lists_added_modified_and_deleted_files_and_counts_changed_lines() -> None:
    before = {"a.py": b"x = 1\ny = 2\n", "b.txt": b"old\n", "gone.txt": b"bye\n", "same.txt": b"same\n"}
    after = {"a.py": b"x = 1\ny = 3\nz = 4\n", "b.txt": b"old\n", "new.txt": b"fresh\n", "same.txt": b"same\n"}
    d = diff_trees(before, after)
    assert (d.added, d.modified, d.deleted) == (["new.txt"], ["a.py"], ["gone.txt"])
    assert d.changed_files == ["a.py", "gone.txt", "new.txt"]
    assert d.diff_lines == 5  # -y=2 +y=3 +z=4 / +fresh / -bye
    assert "--- a/a.py" in d.diff and "+++ b/a.py" in d.diff and "+y = 3" in d.diff
    assert "--- /dev/null" in d.diff and "+++ /dev/null" in d.diff


def test_identical_trees_have_no_diff() -> None:
    files = {"a": b"1\n"}
    d = diff_trees(files, dict(files))
    assert d.changed_files == [] and d.diff == "" and d.diff_lines == 0


def test_a_binary_change_is_reported_without_a_text_diff() -> None:
    d = diff_trees({"logo.png": b"\x89PNG\x00\x01"}, {"logo.png": b"\x89PNG\x00\x02"})
    assert d.modified == ["logo.png"] and "Binary file logo.png changed" in d.diff and d.diff_lines == 1
    assert is_text(b"plain") and not is_text(b"a\x00b") and not is_text(b"\xff\xfe")


def test_a_huge_diff_is_cut_and_says_so() -> None:
    d = diff_trees({}, {"big.txt": ("line\n" * 100_000).encode()})
    assert d.truncated and len(d.diff) <= MAX_DIFF_CHARS + 40 and d.diff.endswith("[diff truncated]\n")
    assert d.diff_lines == 100_000, "the size of the change is counted before the text is cut"


def test_reading_a_tree_skips_links_caches_and_version_control(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    (tmp_path / "link").symlink_to("/etc/passwd")
    (tmp_path / "big.bin").write_bytes(b"x" * 50)
    tree = read_tree(tmp_path, max_file_bytes=10)
    assert sorted(tree) == ["big.bin", "src/a.py"]
    assert tree["big.bin"] == b"<50 bytes>", "a file that is too large still shows up as a change, by its size"


def test_protected_files_are_put_back_and_added_ones_removed() -> None:
    baseline = {"src/a.py": b"old", "tests/test_a.py": b"orig", "tests/test_b.py": b"orig_b"}
    after = {
        "src/a.py": b"fixed",
        "tests/test_a.py": b"edited",  # edited to pass
        "tests/test_extra.py": b"added",  # a new test that always passes
        # tests/test_b.py was deleted
    }
    verified, restored = restore_protected(after, baseline, ("tests/**",))
    assert verified["src/a.py"] == b"fixed", "the agent's own change to the source is kept"
    assert verified["tests/test_a.py"] == b"orig" and verified["tests/test_b.py"] == b"orig_b"
    assert "tests/test_extra.py" not in verified
    assert restored == ["tests/test_a.py", "tests/test_b.py", "tests/test_extra.py"]


def test_only_changed_and_named_text_files_are_captured() -> None:
    after = {"a.txt": b"alpha", "b.txt": b"beta", "bin": b"\x00\x01", "c.txt": b"gamma"}
    got = captured_contents(after, changed=["a.txt", "bin"], wanted=["c.txt", "missing.txt"])
    assert got == {"c.txt": "gamma", "a.txt": "alpha"}
    assert (
        captured_contents({f"{i}.txt": b"x" for i in range(30)}, [f"{i}.txt" for i in range(30)], []).keys().__len__()
        == 20
    )


def test_write_tree_creates_nested_files(tmp_path: Path) -> None:
    write_tree(tmp_path / "out", {"a/b/c.txt": b"c", "d.txt": b"d"})
    assert (tmp_path / "out" / "a" / "b" / "c.txt").read_bytes() == b"c"
    assert read_tree(tmp_path / "out") == {"a/b/c.txt": b"c", "d.txt": b"d"}

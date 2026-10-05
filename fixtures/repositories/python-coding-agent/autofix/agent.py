import subprocess
from pathlib import Path

SYSTEM_PROMPT = "You are a coding agent. Make the smallest change that completes the task, run the tests, never modify unrelated files."

def run_tests(repo: Path) -> int:
    return subprocess.run(["pytest", "-q"], cwd=repo).returncode

def edit_file(path: str, old: str, new: str) -> None:
    p = Path(path)
    p.write_text(p.read_text().replace(old, new))

def apply_patch(diff: str) -> None:
    subprocess.run(["git", "apply"], input=diff.encode())

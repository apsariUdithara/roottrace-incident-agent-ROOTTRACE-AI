"""Prove every seeded bug behaves as designed. Free: no model or sandbox calls.

For each scenario, on a temporary copy of PayStream:
  1. its hidden test passes on the healthy code,
  2. with the bug planted, the visible tests still pass (the bug slips through),
  3. with the bug planted, its hidden test fails (the bug is caught).

Run from the repo root: python demo-target/scripts/verify_scenarios.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scenarios import SCENARIOS, apply_edits

HERE = Path(__file__).resolve().parent
PAYSTREAM = HERE.parent / "paystream"
HIDDEN = HERE.parents[1] / "eval" / "hidden_tests"


def pytest_ok(root: Path, *args: str) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           "-c", "pyproject.toml", "--rootdir", ".", *args]
    return subprocess.run(cmd, cwd=root, capture_output=True).returncode == 0


def check(scenario: dict) -> list[str]:
    problems = []
    # Copied into the project copy, as the sandbox will do: running a test file that
    # lives outside the project makes pytest scan parent folders (fails on Windows).
    hidden = "hidden/" + scenario["hidden_test"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "paystream"
        shutil.copytree(PAYSTREAM, root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        (root / "hidden").mkdir()
        shutil.copy(HIDDEN / scenario["hidden_test"], root / hidden)
        if not pytest_ok(root, hidden):
            problems.append("hidden test fails on healthy code")
        apply_edits(root, scenario["edits"])
        if not pytest_ok(root, "tests"):
            problems.append("bug breaks the visible tests (too obvious)")
        if pytest_ok(root, hidden):
            problems.append("hidden test does not catch the bug")
    return problems


if __name__ == "__main__":
    failed = 0
    for s in SCENARIOS:
        problems = check(s)
        failed += bool(problems)
        print(f"{'OK  ' if not problems else 'FAIL'} scenario {s['id']}: {s['title']}"
              + ("" if not problems else "  <- " + "; ".join(problems)))
    print(f"\n{len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios behave as designed")
    sys.exit(1 if failed else 0)

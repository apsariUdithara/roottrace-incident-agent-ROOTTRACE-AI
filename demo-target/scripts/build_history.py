"""Build the PayStream git repo with a realistic history. Free: git only.

- main: the healthy service, built up over ~30 commits (June to early September 2026),
  including the INC-017 episode (pool raised to 60, then restored to 20).
- release/vX + tag vX per scenario: main, then the scenario's bad commit, then a
  release commit bumping the version. Each release contains exactly one bug.

Finally checks every release: visible tests pass and its hidden test fails.

Run from the repo root: python demo-target/scripts/build_history.py
Output: demo-target/build/paystream (a standalone git repo; not part of RootTrace's repo)
"""
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from scenarios import SCENARIOS, apply_edits

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "paystream"
OUT = HERE.parent / "build" / "paystream"
HIDDEN = HERE.parents[1] / "eval" / "hidden_tests"

MAYA = "Maya Chen <maya@paystream.dev>"
TOM = "Tom Okafor <tom@paystream.dev>"
PRIYA = "Priya Nair <priya@paystream.dev>"
LUKAS = "Lukas Berg <lukas@paystream.dev>"
CONFIG = "src/paystream/database/config.py"

# (date, author, message, files to copy from the healthy service) or
# (date, author, message, [(file, old, new)]) for edits to files already in the repo.
MAIN = [
    ("2026-06-01T10:12", MAYA, "chore: project skeleton", ["README.md", "pyproject.toml", "requirements.txt", "src/paystream/__init__.py"]),
    ("2026-06-01T15:40", MAYA, "feat: settings loaded from environment", ["src/paystream/settings.py"]),
    ("2026-06-02T09:55", TOM, "feat: database connection settings", ["src/paystream/database/__init__.py", CONFIG]),
    ("2026-06-02T14:20", TOM, "feat: users and payments schema migrations", ["src/paystream/database/migrations.py"]),
    ("2026-06-03T11:05", MAYA, "feat: order totals with Decimal", ["src/paystream/payments/__init__.py", "src/paystream/payments/service.py"]),
    ("2026-06-03T16:30", MAYA, "test: order totals and charges", ["tests/test_payments.py"]),
    ("2026-06-04T10:45", PRIYA, "feat: session tokens with one-hour TTL", ["src/paystream/auth/__init__.py", "src/paystream/auth/tokens.py"]),
    ("2026-06-04T17:10", PRIYA, "test: token expiry", ["tests/test_auth.py"]),
    ("2026-06-05T09:30", LUKAS, "feat: card gateway client with retries and backoff", ["src/paystream/payments/gateway.py"]),
    ("2026-06-05T13:15", LUKAS, "test: gateway retries", ["tests/test_gateway.py"]),
    ("2026-06-08T11:00", LUKAS, "feat: gateway webhook handler", ["src/paystream/payments/webhooks.py"]),
    ("2026-06-08T15:25", LUKAS, "test: webhook parsing", ["tests/test_webhooks.py"]),
    ("2026-06-09T10:40", TOM, "feat: user search for support console", ["src/paystream/users/__init__.py", "src/paystream/users/search.py"]),
    ("2026-06-09T16:05", TOM, "test: migrations and user search", ["tests/test_database.py"]),
    ("2026-06-10T11:20", MAYA, "feat: HTTP API", ["src/paystream/app.py"]),
    ("2026-06-10T15:50", MAYA, "test: API endpoints", ["tests/test_app.py"]),
    ("2026-06-11T09:45", TOM, "perf: raise DB pool size to 60",
     [(CONFIG, '"PAYSTREAM_DB_POOL_SIZE", "20"', '"PAYSTREAM_DB_POOL_SIZE", "60"')]),
    ("2026-06-11T13:30", PRIYA, "chore: production deploy config", ["deploy/production.env"]),
    ("2026-06-16T17:45", TOM, "fix: restore DB pool size to 20 (INC-017)",
     [(CONFIG, '"PAYSTREAM_DB_POOL_SIZE", "60"', '"PAYSTREAM_DB_POOL_SIZE", "20"')]),
    ("2026-06-17T10:10", TOM, "docs: database pooling rules after INC-017", ["docs/database.md"]),
    ("2026-06-18T14:00", MAYA, "docs: architecture overview", ["docs/architecture.md"]),
    ("2026-06-23T11:35", MAYA, "docs: money handling and idempotency", ["docs/payments.md"]),
    ("2026-06-29T15:20", PRIYA, "docs: authentication and time handling", ["docs/authentication.md"]),
    ("2026-07-15T09:50", PRIYA, "docs: deployment and configuration checklist", ["docs/deployment.md"]),
    ("2026-07-29T16:40", LUKAS, "docs: API reference", ["docs/api-reference.md"]),
]


def git(*args: str, date: str | None = None, author: str | None = None) -> str:
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date + ":00"
    if author:
        name, email = author[:-1].split(" <")
        env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    return subprocess.run(["git", *args], cwd=OUT, env=env, check=True, capture_output=True, text=True).stdout


def commit(date: str, author: str, message: str, change) -> None:
    if change and isinstance(change[0], tuple):
        apply_edits(OUT, change)
    else:
        for rel in change:
            (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(SRC / rel, OUT / rel)
    git("add", "-A")
    git("commit", "-q", "-m", message, date=date, author=author)


def bump_version(version: str) -> list[tuple[str, str, str]]:
    current = (OUT / "pyproject.toml").read_text(encoding="utf-8").split('version = "')[1].split('"')[0]
    return [("pyproject.toml", f'version = "{current}"', f'version = "{version}"'),
            ("src/paystream/app.py", f'version="{current}"', f'version="{version}"')]


def check_release(tag: str, hidden_test: str) -> list[str]:
    """Visible tests must pass and the hidden test must fail at this tag."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        subprocess.run(["git", "clone", "-q", "--branch", tag, str(OUT), str(root)], check=True, capture_output=True)
        (root / "hidden").mkdir()
        shutil.copy(HIDDEN / hidden_test, root / "hidden" / hidden_test)
        run = lambda *a: subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *a],
                                        cwd=root, capture_output=True).returncode == 0
        problems = []
        if not run("tests"):
            problems.append("visible tests fail")
        if run("hidden"):
            problems.append("hidden test passes (bug not present)")
        return problems


if __name__ == "__main__":
    # Empty the folder but keep it: Windows can't delete a folder a terminal is open in.
    OUT.mkdir(parents=True, exist_ok=True)
    for child in OUT.iterdir():
        if child.is_dir():
            shutil.rmtree(child, onerror=lambda f, p, _: (os.chmod(p, 0o700), f(p)))
        else:
            child.unlink()
    git("init", "-q", "-b", "main")
    for step in MAIN:
        commit(*step)

    for s in sorted(SCENARIOS, key=lambda s: s["deploy_at"]):
        deploy = datetime.fromisoformat(s["deploy_at"])
        tag = s["release"]
        git("checkout", "-q", "-b", f"release/{tag}", "main")
        bad_at = (deploy - timedelta(hours=3, minutes=17)).isoformat(timespec="minutes")
        release_at = (deploy - timedelta(minutes=25)).isoformat(timespec="minutes")
        author = [MAYA, TOM, PRIYA, LUKAS][s["id"] % 4]
        commit(bad_at, author, s["commit"], s["edits"])
        commit(release_at, MAYA, f"chore: release {tag}", bump_version(tag.lstrip("v")))
        git("tag", tag)
        git("checkout", "-q", "main")

    print(f"built {OUT} with {len(git('log', '--oneline', 'main').splitlines())} commits on main "
          f"and {len(SCENARIOS)} release tags\n")
    failed = 0
    for s in sorted(SCENARIOS, key=lambda s: s["release"]):
        problems = check_release(s["release"], s["hidden_test"])
        failed += bool(problems)
        print(f"{'OK  ' if not problems else 'FAIL'} {s['release']}  scenario {s['id']}: {s['title']}"
              + ("" if not problems else "  <- " + "; ".join(problems)))
    sys.exit(1 if failed else 0)

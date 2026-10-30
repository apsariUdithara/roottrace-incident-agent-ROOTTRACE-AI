"""Run a project's tests in Nebius Token Factory Sandboxes.

Uses the official ConTree CLI (`python -m contree_cli -o json`), whose behaviour we confirmed by
hand in Phase 0. Each test run starts from a cached environment image (the project's
dependencies installed once per dependency set), copies the code in, runs one command and is
thrown away (`--disposable`), so runs never affect each other.

Credentials: the CLI's saved profile (`contree auth`), or NEBIUS_API_KEY + NEBIUS_AI_PROJECT.
Never upload secrets: only the repository snapshot and the patch go into a sandbox.
"""

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

BASE_IMAGE = "tag:python:3.12"
ENV_TAG_PREFIX = "roottrace/env"
WORKDIR = "/work"
SETUP_TIMEOUT_S = 600
TEST_TIMEOUT_S = 300
DEPENDENCY_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")


class SandboxError(Exception):
    """The sandbox could not run the command at all (as opposed to tests failing)."""


@dataclass
class RunResult:
    command: str
    exit_code: int | None  # None if the run timed out
    stdout: str
    stderr: str
    duration_ms: int
    operation_id: str  # the Nebius operation: proof the run happened in a sandbox
    image: str
    passed: int | None = None
    failed: int | None = None


SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors)\b")


def pytest_counts(output: str) -> tuple[int | None, int | None]:
    """Passed and failed counts from pytest's summary line, e.g. '1 failed, 3 passed'."""
    found = {}
    for n, word in SUMMARY.findall(output):
        key = "failed" if word.startswith(("failed", "error")) else "passed"
        found[key] = found.get(key, 0) + int(n)
    if not found:
        return None, None
    return found.get("passed", 0), found.get("failed", 0)


def default_setup_command(repo: Path) -> str | None:
    if (repo / "requirements.txt").exists():
        return "pip install -q -r requirements.txt"
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        return "pip install -q ."
    return None


def profile_path() -> Path:
    """Where the ConTree CLI saves its login profile."""
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config) / "contree" / "auth.ini"


_login = asyncio.Lock()


async def ensure_login() -> None:
    """On a server there is no `contree auth` profile, and the CLI won't read the key from the
    environment for other commands (found on the free Render deploy: every sandbox call failed).
    `contree auth` does read NEBIUS_API_KEY and NEBIUS_AI_PROJECT, so save a profile from them
    once. A machine that already has a profile (a laptop after `contree auth`) is left alone."""
    async with _login:
        if profile_path().exists():
            return
        if not (os.environ.get("NEBIUS_API_KEY") and os.environ.get("NEBIUS_AI_PROJECT")):
            return  # nothing to log in with; the CLI's own error will say so
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "contree_cli",
            "auth",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), 60)
        if proc.returncode != 0:
            detail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
            raise SandboxError(f"sandbox login failed: {detail[0]}")


class NebiusSandbox:
    async def _cli(self, *args: str, timeout: int) -> dict:
        await ensure_login()
        session = f"rt-{uuid.uuid4().hex[:12]}"  # isolate concurrent runs from each other
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "contree_cli",
            "-o",
            "json",
            "-S",
            session,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout + 60)
        except TimeoutError:
            proc.kill()
            raise SandboxError("the sandbox did not answer in time") from None
        text = out.decode("utf-8", "replace").strip()
        try:
            return json.loads(text.splitlines()[-1]) if text else {}
        except json.JSONDecodeError:
            detail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
            raise SandboxError(f"sandbox CLI failed: {detail[0]}") from None

    async def environment(self, repo: Path, setup_command: str | None) -> str:
        """The image with the project's dependencies, built once per dependency set."""
        setup = setup_command or default_setup_command(repo)
        if not setup:
            return BASE_IMAGE
        fingerprint = hashlib.sha256(setup.encode())
        for name in DEPENDENCY_FILES:
            if (repo / name).exists():
                fingerprint.update(name.encode() + (repo / name).read_bytes())
        tag = f"{ENV_TAG_PREFIX}-{fingerprint.hexdigest()[:16]}:latest"

        listed = await self._cli("images", f"--prefix={tag.split(':')[0]}", timeout=60)
        if listed:  # a JSON list (or object) of matching images
            return f"tag:{tag}"
        built = await self._cli(
            "run",
            "--use",
            BASE_IMAGE,
            "-C",
            WORKDIR,
            "--file",
            f"{repo}:{WORKDIR}",
            "-t",
            str(SETUP_TIMEOUT_S),
            "-s",
            "--",
            setup,
            timeout=SETUP_TIMEOUT_S,
        )
        image = built.get("result_image_uuid") or (built.get("result") or {}).get("image")
        if built.get("exit_code") != 0 or not image:
            raise SandboxError(f"installing dependencies failed: {built.get('stderr', '')[-500:]}")
        await self._cli("tag", image, tag, timeout=60)
        return f"tag:{tag}"

    async def run(self, image: str, workdir: Path, command: str) -> RunResult:
        """Copy `workdir` into a fresh sandbox, run `command`, discard the sandbox."""
        start = time.perf_counter()
        r = await self._cli(
            "run",
            "--use",
            image,
            "-D",
            "-C",
            WORKDIR,
            "--file",
            f"{workdir}:{WORKDIR}",
            "-t",
            str(TEST_TIMEOUT_S),
            "-s",
            "--",
            command,
            timeout=TEST_TIMEOUT_S,
        )
        if not r.get("uuid"):
            raise SandboxError(f"sandbox run failed: {r.get('error') or 'no result'}")
        state = ((r.get("metadata") or {}).get("result") or {}).get("state") or {}
        stdout, stderr = r.get("stdout", ""), r.get("stderr", "")
        passed, failed = pytest_counts(stdout + "\n" + stderr)
        return RunResult(
            command=command,
            exit_code=None if state.get("timed_out") else r.get("exit_code"),
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.perf_counter() - start) * 1000),
            operation_id=r["uuid"],
            image=image,
            passed=passed,
            failed=failed,
        )

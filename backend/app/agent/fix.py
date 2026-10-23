"""Fix Generator helpers: snapshots, the files involved, applying a fix and diffing it.

The model writes each changed file in full (models are unreliable at diff line numbers but
good at rewriting a small file); `unified_diff` then computes the diff, which cannot be wrong.
"""

import difflib
import io
import re
import subprocess
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field

from app.tools.git import safe_repo_path

MAX_FILE_CHARS = 12000  # files larger than this are not offered to the model in full
MAX_FILES = 4


class FileChange(BaseModel):
    path: str = Field(description="Path relative to the repository root")
    content: str = Field(description="The file's complete new content")
    explanation: str = ""


class PatchOut(BaseModel):
    test: FileChange
    changes: list[FileChange] = Field(min_length=1, max_length=MAX_FILES)
    explanation: str


class FixJudgement(BaseModel):
    addresses_root_cause: bool
    notes: str


class PrOut(BaseModel):
    title: str = Field(max_length=120)
    body: str


def snapshot(repo: Path, ref: str, dest: Path) -> None:
    """Write the repository's files at `ref` into `dest` (no .git, no working-tree changes)."""
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=zip", ref],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout
    zipfile.ZipFile(io.BytesIO(archive)).extractall(dest)


CODE_REF = re.compile(r"^([\w./-]+\.\w+)(?: |$)")


def involved_files(facts: list[dict], repo: Path, ref: str) -> list[str]:
    """Code files the evidence points at: cited files, and files changed by cited commits."""
    paths: list[str] = []
    for fact in facts:
        ref_text = fact["source_ref"]
        if fact["source_type"] == "code" and (m := CODE_REF.match(ref_text)):
            paths.append(m.group(1))
        elif fact["source_type"] == "commit" and ref_text.startswith("commit "):
            sha = ref_text.split()[1]
            changed = subprocess.run(
                ["git", "-C", str(repo), "show", "--name-only", "--format=", sha],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
            ).stdout.split()
            paths += [p for p in changed if p.endswith(".py")]
    unique = list(dict.fromkeys(p for p in paths if not p.startswith("tests/")))
    return unique[:MAX_FILES]


def read_files(root: Path, paths: list[str]) -> str:
    blocks = []
    for path in paths:
        file = root / path
        if file.is_file():
            text = file.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
            blocks.append(f"--- {path}\n{text}")
    return "\n\n".join(blocks) or "(none found; infer the file from the evidence)"


def test_files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("test_*.py"))


def apply(root: Path, changes: list[FileChange]) -> None:
    """Write each change into the snapshot. Paths must stay inside the repository."""
    for change in changes:
        target = root / safe_repo_path(change.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.content, encoding="utf-8", newline="\n")


def unified_diff(root: Path, changes: list[FileChange]) -> str:
    """The diff of `changes` against the files currently in `root`."""
    parts = []
    for change in changes:
        path = safe_repo_path(change.path)
        before = (root / path).read_text(encoding="utf-8") if (root / path).exists() else ""
        parts += difflib.unified_diff(
            before.splitlines(keepends=True),
            change.content.splitlines(keepends=True),
            fromfile=f"a/{path}" if before else "/dev/null",
            tofile=f"b/{path}",
        )
    return "".join(parts)


def touches_existing_tests(base: Path, files: list[FileChange]) -> list[str]:
    """Paths of existing test files the patch would change. Empty means the patch is allowed."""
    blocked = []
    for change in files:
        exists = (base / change.path).exists()
        is_test = change.path.startswith("tests/") or Path(change.path).name.startswith("test_")
        if exists and is_test:
            blocked.append(change.path)
    return blocked


def example_test(root: Path, involved: list[str]) -> str:
    """One existing test file, in full, so the model copies the project's imports and style.

    Prefers a test named after a file the fix touches (config.py -> test_config.py or a test
    file in the same area); otherwise the first test file.
    """
    tests = test_files(root)
    if not tests:
        return "(no existing tests)"
    stems = {Path(p).stem for p in involved} | {Path(p).parent.name for p in involved}
    chosen = next((t for t in tests if any(stem and stem in t for stem in stems)), tests[0])
    return f"--- {chosen}\n" + (root / chosen).read_text(encoding="utf-8")[:MAX_FILE_CHARS]

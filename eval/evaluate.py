"""Evaluate RootTrace on the seeded PayStream scenarios and write the numbers to the README.

Every case runs a REAL investigation: the model cache is off (a cache would replay old answers
and fake the scores), and fixes are tested in Nebius Sandboxes. Then each result is scored:

    root_cause      a model judges the report's root cause against the expected one
    keywords        at least one expected keyword appears in the root cause (a code check)
    evidence        share of the expected sources (commit, files, docs, incidents) the facts cite
    citations       every [F3]-style label in the report refers to evidence that exists
    tools           the investigation used the tools a good one needs
    fix             the agent's patch passes the HIDDEN test (never shown to the agent) in a sandbox
    no_false_fix    healthy cases: no root cause claimed and no fix proposed

Run from the repository root, with Postgres running:
    backend\\.venv\\Scripts\\python eval\\evaluate.py                 # all cases
    backend\\.venv\\Scripts\\python eval\\evaluate.py --scenarios 1,4 --first-question-only
Cost: about $0.05 per case with Ultra pointed at Super, $0.10-0.15 with real Ultra.
"""

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
os.chdir(BACKEND)  # the backend reads its settings (.env) from here
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "demo-target" / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

import yaml  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.agent.fix import snapshot  # noqa: E402
from app.agent.followup import evidence_lines  # noqa: E402
from app.agent.run import run_investigation  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.llm.router import LLMRouter  # noqa: E402
from app.models import EvaluationRun, Patch, PatchStatus, SpanKind, TraceSpan  # noqa: E402
from app.sandbox import NebiusSandbox, SandboxError  # noqa: E402

spec = importlib.util.spec_from_file_location("ingest", BACKEND / "scripts" / "ingest_paystream.py")
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)

REPO = ROOT / "demo-target" / "build" / "paystream"
HIDDEN = ROOT / "eval" / "hidden_tests"
RESULTS = ROOT / "eval" / "results"
LABEL = re.compile(r"\b([FI]\d+)\b")


class Judgement(BaseModel):
    correct: bool
    reason: str


JUDGE = """Expected root cause of an incident:
{expected}

Root cause given in an incident report:
{actual}

Does the report identify the same root cause? The wording may differ; the mechanism must match
(the same change, the same faulty behaviour). A vaguer answer that names the right change and
the right failure counts; naming only the symptom, or a different cause, does not."""


def progress(event) -> None:
    """One short line per step and tool call, so a long run shows it is moving."""
    p = event.payload
    if event.type == "step_started":
        print(f"    {p.step}" + (f" (round {p.round})" if p.round > 1 else ""), flush=True)
    elif event.type == "tool_call":
        print(f"      → {p.tool}", flush=True)


def load_cases(only: set[int] | None, first_only: bool) -> list[dict]:
    spec = yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in SCENARIOS}
    cases = []
    for sid, expect in spec["scenarios"].items():
        if only and sid not in only:
            continue
        scenario = by_id[sid]
        reported = datetime.fromisoformat(scenario["deploy_at"]).replace(tzinfo=UTC)
        for n, question in enumerate(expect["questions"][: 1 if first_only else None], start=1):
            cases.append(
                {
                    "id": f"s{sid}-q{n}",
                    "scenario": scenario,
                    "question": question,
                    "ref": scenario["release"],
                    "reported_at": reported + timedelta(minutes=50),
                    "expect": expect,
                }
            )
    if not only:
        for n, case in enumerate(spec["healthy"], start=1):
            cases.append(
                {
                    "id": f"healthy-{n}",
                    "scenario": None,
                    "question": case["question"],
                    "ref": case["ref"],
                    "expect": None,
                    "reported_at": datetime.fromisoformat(case["reported_at"]),
                }
            )
    return cases


def bad_commit(scenario: dict) -> str:
    """The `commit <sha10>` reference of the scenario's bad commit, as the tools cite it."""
    sha = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "log",
            scenario["release"],
            "--fixed-strings",
            f"--grep={scenario['commit']}",
            "--format=%h",
            "--abbrev=10",
            "-n1",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"commit {sha}"


async def hidden_test_passes(
    sandbox: NebiusSandbox, image: str, ref: str, diff: str, test_name: str
) -> bool:
    """Apply the agent's patch to the deployed code and run the hidden test in a sandbox."""
    with tempfile.TemporaryDirectory(prefix="roottrace-eval-") as tmp:
        work = Path(tmp) / "work"
        snapshot(REPO, ref, work)
        (Path(tmp) / "fix.patch").write_text(diff, encoding="utf-8", newline="\n")
        applied = subprocess.run(
            ["git", "apply", str(Path(tmp) / "fix.patch")], cwd=work, capture_output=True
        )
        if applied.returncode != 0:
            return False
        shutil.copy(HIDDEN / test_name, work / "tests" / f"hidden_{test_name}")
        result = await sandbox.run(image, work, f"pytest -q tests/hidden_{test_name}")
        return result.exit_code == 0


async def score(session, router, sandbox, case, inv, image) -> dict:
    report = inv.report or {}
    root_cause = report.get("root_cause") or ""
    evidence = await evidence_lines(session, inv.id)
    facts = [e for e in evidence.values() if e["kind"] == "fact"]
    cited = set(LABEL.findall(report.get("summary", "")))
    tools = set(
        (
            await session.scalars(
                select(TraceSpan.name).where(
                    TraceSpan.investigation_id == inv.id, TraceSpan.kind == SpanKind.TOOL
                )
            )
        ).all()
    )
    patch = await session.scalar(
        select(Patch).where(Patch.investigation_id == inv.id).order_by(Patch.attempt.desc())
    )
    row = {
        "id": case["id"],
        "ref": case["ref"],
        "status": str(inv.status),
        "root_cause": root_cause,
        "confidence": inv.confidence,
        "citations": not (cited - set(evidence)),
        "fix_status": str(patch.status) if patch else "none",
        "cost_usd": float(inv.total_cost_usd or 0),
        "tokens": inv.total_tokens,
        "seconds": round((inv.finished_at - inv.started_at).total_seconds())
        if inv.finished_at
        else None,
        "investigation_id": str(inv.id),
    }
    expect = case["expect"]
    if expect is None:  # healthy case
        # Correct on a healthy version: no root cause claimed and no fix attempted at all.
        row["no_false_fix"] = patch is None and not root_cause
        return row

    judged = await router.complete(
        "super",
        [
            {
                "role": "user",
                "content": JUDGE.format(
                    expected=expect["root_cause"], actual=root_cause or "(none)"
                ),
            }
        ],
        schema=Judgement,
        max_tokens=1500,
        name="eval_judge",
    )
    wanted = [bad_commit(case["scenario"]) if e == "bad_commit" else e for e in expect["evidence"]]
    refs = " | ".join(f["source_ref"] or "" for f in facts)
    found = [w for w in wanted if w in refs]
    row |= {
        "root_cause_correct": judged.parsed.correct,
        "judge_reason": judged.parsed.reason,
        "keywords": any(k.lower() in root_cause.lower() for k in expect["keywords"]),
        "evidence": round(len(found) / len(wanted), 2),
        "evidence_missing": [w for w in wanted if w not in found],
        "tools": set(expect["tools"]) <= tools,
        "fix": None,
    }
    if patch and patch.status == PatchStatus.VERIFIED and image:
        try:
            row["fix"] = await hidden_test_passes(
                sandbox, image, case["ref"], patch.diff, case["scenario"]["hidden_test"]
            )
        except (SandboxError, subprocess.CalledProcessError) as exc:
            row["fix_error"] = str(exc)[:200]
    elif patch is None or patch.status != PatchStatus.VERIFIED:
        row["fix"] = False
    return row


def share(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return (sum(map(float, values)) / len(values)) if values else None


def summarise(rows: list[dict]) -> dict:
    bugs = [r for r in rows if "root_cause_correct" in r]
    healthy = [r for r in rows if "no_false_fix" in r]
    return {
        "cases": len(rows),
        "root_cause_accuracy": share(bugs, "root_cause_correct"),
        "keyword_hit_rate": share(bugs, "keywords"),
        "evidence_recall": share(bugs, "evidence"),
        "citations_valid": share(rows, "citations"),
        "tool_selection": share(bugs, "tools"),
        "verified_fix_rate": share(bugs, "fix"),
        "no_false_fix_rate": share(healthy, "no_false_fix"),
        "avg_confidence_correct": share([r for r in bugs if r["root_cause_correct"]], "confidence"),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "avg_seconds": share(rows, "seconds"),
    }


def pct(v):
    return "–" if v is None else f"{v:.0%}"


def markdown(summary: dict, rows: list[dict], models: dict) -> str:
    lines = [
        f"*Run {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · {summary['cases']} cases · "
        f"Ultra = `{models['ultra']}` · total ${summary['total_cost_usd']:.2f}*",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Root cause correct | {pct(summary['root_cause_accuracy'])} |",
        f"| Expected evidence cited | {pct(summary['evidence_recall'])} |",
        f"| Citations valid | {pct(summary['citations_valid'])} |",
        f"| Tool selection | {pct(summary['tool_selection'])} |",
        f"| Verified fix passes the hidden test | {pct(summary['verified_fix_rate'])} |",
        f"| No false fix on healthy versions | {pct(summary['no_false_fix_rate'])} |",
        f"| Avg time per investigation | {summary['avg_seconds'] or 0:.0f}s |",
        "",
        "| Case | Root cause | Evidence | Fix (hidden test) | Confidence | Cost |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "no_false_fix" in r:
            verdict = "✅ none claimed" if r["no_false_fix"] else "❌ false fix"
            lines.append(f"| {r['id']} (healthy) | {verdict} | – | – | – | ${r['cost_usd']:.3f} |")
            continue
        fix = {True: "✅", False: "❌", None: "–"}[r["fix"]]
        lines.append(
            f"| {r['id']} | {'✅' if r['root_cause_correct'] else '❌'} | {r['evidence']:.0%} | "
            f"{fix} ({r['fix_status']}) | {pct(r['confidence'])} | ${r['cost_usd']:.3f} |"
        )
    failed = [r for r in rows if r.get("root_cause_correct") is False or r.get("fix") is False]
    if failed:
        lines += ["", "**Failures, not hidden:**"]
        lines += [
            f"- `{r['id']}`: {r.get('judge_reason', '')[:200]} "
            f"Missing evidence: {r.get('evidence_missing') or 'none'}."
            for r in failed
        ]
    return "\n".join(lines)


def update_readme(table: str) -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- EVAL_RESULTS_START -->", "<!-- EVAL_RESULTS_END -->"
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    readme.write_text(f"{before}{start}\n{table}\n{end}{after}", encoding="utf-8")


async def main(args) -> None:
    only = {int(x) for x in args.scenarios.split(",")} if args.scenarios else None
    cases = load_cases(only, args.first_question_only)
    settings = Settings(app_env="test")  # "test": the model cache is off for honest numbers
    models = {
        "ultra": settings.nemotron_ultra,
        "super": settings.nemotron_super,
        "nano": settings.nemotron_nano,
    }
    print(f"{len(cases)} cases · Ultra = {models['ultra']}")
    sandbox, rows, started = NebiusSandbox(), [], time.perf_counter()

    async with SessionLocal() as session:
        index_router = LLMRouter(settings)
        projects = {}
        for ref in dict.fromkeys(c["ref"] for c in cases):
            print(f"indexing PayStream@{ref} ...", flush=True)
            project, _ = await ingest.load(session, index_router, ref, f"PayStream@{ref}")
            # Plain values: a rollback inside a run expires ORM objects, and reading an
            # expired attribute later (outside an await) crashes with MissingGreenlet.
            projects[ref] = (project.id, project.owner_id)
        for case in cases:
            project_id, owner_id = projects[case["ref"]]
            router = LLMRouter(settings)
            print(f"\n▶ {case['id']} @ {case['ref']}: {case['question']}")
            inv, _ = await run_investigation(
                session,
                router,
                project_id,
                owner_id,
                case["question"],
                ref=case["ref"],
                reported_at=case["reported_at"],
                listeners=[progress],
            )
            await session.refresh(inv)  # load fields the database filled in (started_at)
            image = None
            if case["scenario"]:
                # A sandbox outage must not end the whole run: retry once, then score this
                # case without the hidden test (its fix counts as not confirmed).
                for wait in (30, None):
                    try:
                        with tempfile.TemporaryDirectory() as tmp:
                            snapshot(REPO, case["ref"], Path(tmp) / "code")
                            image = await sandbox.environment(Path(tmp) / "code", None)
                        break
                    except SandboxError as exc:
                        print(f"  sandbox unavailable: {exc}")
                        if wait:
                            await asyncio.sleep(wait)
            row = await score(session, router, sandbox, case, inv, image)
            await session.commit()
            rows.append(row)
            print(
                "  "
                + json.dumps(
                    {
                        k: row[k]
                        for k in row
                        if k
                        in (
                            "root_cause_correct",
                            "evidence",
                            "fix",
                            "fix_status",
                            "no_false_fix",
                            "confidence",
                            "cost_usd",
                        )
                    }
                )
            )

        summary = summarise(rows)
        session.add(
            EvaluationRun(
                git_sha=subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
                ).stdout.strip(),
                model_config=models,
                metrics=summary,
                cases=rows,
            )
        )
        await session.commit()

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (RESULTS / f"{stamp}.json").write_text(
        json.dumps({"summary": summary, "models": models, "cases": rows}, indent=2, default=str),
        encoding="utf-8",
    )
    table = markdown(summary, rows, models)
    if args.write_readme:
        update_readme(table)
    print(
        "\n" + table + f"\n\nfinished in {time.perf_counter() - started:.0f}s; "
        f"results in eval/results/{stamp}.json"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", help="comma-separated scenario ids, e.g. 1,4")
    parser.add_argument("--first-question-only", action="store_true")
    parser.add_argument(
        "--write-readme",
        action="store_true",
        help="replace the results table in README.md (use for full runs only)",
    )
    asyncio.run(main(parser.parse_args()))

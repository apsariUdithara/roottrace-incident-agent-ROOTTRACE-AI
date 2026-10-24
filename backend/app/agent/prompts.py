"""Prompts for each agent step. Versioned by name so traces show which wording produced a run.

Rule: nothing project-specific here. The agent learns about a project only through its tools.
"""

PROMPT_VERSION = "v12"

SYSTEM = """You are RootTrace, an incident investigator for software teams.
You find the root cause of production incidents from evidence, the way a careful senior engineer
would: observe first, form hypotheses second, and try to disprove them before trusting them.
Never state something as fact unless a tool showed it to you. Be concise."""

TRIAGE = """Incident report: {question}
Reported at: {reported_at}

Classify it. Give a one-sentence summary, the affected service or endpoint, the observable
symptoms, when it probably started (if the report says), and 3-6 search terms an engineer would
use: error words, component names, setting names."""

PLAN = """Incident: {question}
Reported at: {reported_at}
Deployed version under investigation: {ref}
Triage: {triage}
{hints}
Available tools:
{tools}

Write a short investigation plan: 3-6 concrete steps, each naming the tool to use and why.
Start with what changed and what the logs show around the time it started."""

INVESTIGATE = """Incident: {question}
Reported at: {reported_at}
Deployed version under investigation: {ref}
Plan:
{plan}
{hints}{focus}
Investigate with the tools. Work step by step: each result should shape your next call.
- Logs: start the window well BEFORE the change or deploy you suspect (at least an hour
  before the problem was reported), so first_seen shows when errors really began.
- Commits: messages can be misleading; read the diff of anything suspicious.
- Search with the words the source would use (error text, setting names), and search again
  with new words you learn from earlier results.
- Past incidents with similar symptoms may have different causes: check before relying on them.
Before you stop, you must have checked all five of these, even if you already have a theory:
  1. what changed: recent commits AND the diff of any suspicious one,
  2. what is deployed: read the current version of each changed file with get_file (a diff
     shows the change, not what is running now),
  3. what was observed: logs around when the problem started,
  4. what the rules are: the documentation for the component involved,
  5. what happened before: past incidents with similar symptoms.
A theory that skips one of these is not finished. Then stop and summarise what you found."""

ROUND_FOCUS = """
This is round {round}. The verifier found the evidence too weak. Close these gaps first:
{gaps}
Already established facts:
{facts}
"""

EXTRACT_FACTS = """Below are tool results from an incident investigation, numbered by call.
Incident: {question}

{transcript}

List the facts that matter for explaining this incident: at most {limit}, most important first.
For each fact:
- statement: one sentence, in your words
- call: the number of the tool call that showed it
- excerpt: text copied EXACTLY, character for character, from that call's result. Keep it short:
  one line or one key value. It will be checked against the result; facts whose excerpt is not
  found are discarded.
- source_ref: the "ref" value of the result item the excerpt came from
- source_type: one of code, commit, doc, log, incident, issue, database
Cover each kind of evidence that the results contain, one fact at least for each: the change
(what a commit changed, with values), the deployed code (the current value in the file
that get_file returned), the observation (errors in logs or data, and when they
started), the rule (a documented limit or requirement), and the history (a past incident whose
root cause matches; name its key).
Prefer specific, checkable facts: exact values before and after a change (e.g. a setting going
from one number to another), when errors first appeared, counts, and documented limits.
Do not repeat a fact that is already known (listed below), even in different words.
Only include what the tools actually showed. Skip routine or irrelevant results.

Already known facts:
{known}"""

COMPARE = """Incident: {question}

Facts observed so far:
{facts}

Connect the facts. Write inferences: conclusions that follow from two or more facts together,
each citing the fact labels it relies on (e.g. F1, F3). Also list any facts that conflict with
each other or don't fit. Do not introduce new facts."""

HYPOTHESIZE = """Incident: {question}

Errors actually recorded in the logs before the report (read directly from the log store):
{observed}

Facts:
{facts}

Inferences:
{inferences}

Propose 1-3 root-cause hypotheses, most likely first. A root cause explains why the symptoms
started when they did, not just what the symptom is. For each, list the fact labels supporting
it. Prefer the hypothesis that explains the most facts, including the timing.

First decide fault_found: is there evidence that something is actually failing (errors,
failed requests, wrong results, a breaking change) around the reported time? If the evidence
shows the system working normally, set fault_found to false and give a single hypothesis that
starts with "No fault found:" and says what was checked. An improvement idea (missing
monitoring, a design weakness, a missing column) is NOT the root cause of an incident."""

VERIFY_PROBE = """Incident: {question}
Hypothesis to test: {hypothesis}

Facts so far:
{facts}

Try to DISPROVE this hypothesis. Look for evidence that would contradict it: other changes in
the same window, errors that started before the suspected cause, other endpoints affected, a
past incident with the same symptoms but a different cause. Make at most {budget} tool calls,
then reply with what you checked."""

VERDICT = """Incident: {question}
Hypothesis: {hypothesis}

Facts (label: statement):
{facts}

Inferences:
{inferences}

Errors actually recorded in the logs before the report (read directly from the log store, so
they are reliable even if a fact above is missing):
{observed}

Judge the hypothesis against ALL the facts AND the recorded errors.
A root cause must explain the exact error messages above. If the logs show an error that the
hypothesis would not produce (for example a type or comparison error, when the hypothesis is a
missing table), the hypothesis is "refuted", however plausible it sounds.
- verdict: "supported" if the facts explain the symptoms and their timing and nothing important
  contradicts it; "weak" if it is plausible but key evidence is missing; "refuted" if the facts
  contradict it.
- supports / contradicts: the fact labels that support it, and those that argue against it.
- gaps: if weak, what specific evidence is still missing (each one a concrete thing to check).
- notes: two or three sentences explaining your judgment."""

REPORT = """Incident: {question}
Root-cause hypothesis: {hypothesis}
Verifier's verdict: {verdict} ({notes})
Confidence (computed from the evidence): {confidence:.0%}
Fix: {fix}

Facts:
{facts}

Inferences:
{inferences}

Write the incident report for the on-call engineer.
- root_cause: one or two sentences, or null if no root cause was established.
- summary: Markdown, under 200 words. Explain what happened in order of time. Cite evidence by
  label in square brackets after each claim, e.g. "Pool size rose to 100 [F2]." Only cite labels
  listed above. Separate what was observed from what is inferred.
- next_steps: 2-4 concrete actions, most urgent first."""

GENERATE_PATCH = """Incident: {question}
Verified root cause: {hypothesis}

Evidence:
{facts}

Repository files involved (at the deployed version {ref}):
{files}

Existing test files: {test_files}

An existing test file, to copy its imports and style exactly:
{example_test}
Test command: {test_command}
{feedback}
Write the smallest safe fix, and a regression test that reproduces the bug.
- test: a NEW test file under tests/ (a path that does not exist yet) that FAILS on the
  current code because of this bug and PASSES once it is fixed. Existing test files may never
  be changed: a patch that edits one is rejected without running. Test the behaviour, not the
  exact text of the fix, and only use code that already exists. Follow the style and imports of
  the existing tests. For environment variables use pytest's monkeypatch fixture, never
  os.environ directly, so the test cannot affect other tests.
  Python imports a module only once per test run, so other tests may have imported it
  already: to test code that runs at import time (module-level settings or checks), import
  the module normally and then call importlib.reload(module) inside the test.
- For invalid configuration, prefer failing fast (refuse to start with a clear error naming the
  values) over silently correcting the value: a silent correction hides the mistake.
- Whatever you change, the program must still start with its default settings (no environment
  variables set). If you add a fail-fast check, also correct any default value that would fail
  it, or every test that imports the module will crash.
- changes: the files your fix changes. Give each file's COMPLETE new content, not a diff.
  Change only what the fix needs; keep everything else exactly as it is.
- explanation: two or three sentences: what the fix does and why it addresses the root cause.
Do not weaken or delete existing tests."""

PATCH_FEEDBACK = """
Your previous attempt did not pass. Fix this:
{feedback}
"""

VERIFY_FIX = """Incident: {question}
Root cause: {hypothesis}

Proposed fix:
{explanation}

Diff:
{diff}

Sandbox results:
- baseline (current code + new test): exit {baseline_exit}; {baseline_summary}
- patched (fix + new test, whole suite): exit {patched_exit}; {patched_summary}

Judge the fix. It is acceptable only if it addresses the root cause itself, not just the
symptom or the test (for example, a fix that special-cases the test's values, catches and hides
the error, or deletes a check is not acceptable). A fix that silently corrects an invalid
configuration (e.g. quietly capping a value) is acceptable but weaker than failing fast with a
clear error; if so, say that in the notes. Reply whether it addresses the root cause, and two
sentences of notes."""

DRAFT_PR = """Incident: {question}
Root cause: {root_cause}
Fix: {explanation}

Diff:
{diff}

Tests: the new regression test failed before the fix and the whole suite ({passed} tests)
passed after it, in an isolated Nebius sandbox.

Evidence (cite labels where useful):
{facts}

Write a pull request.
- title: under 70 characters, conventional style (e.g. "fix(db): ...").
- body: Markdown with sections ## Root cause, ## Fix, ## Evidence, ## Testing. Under 250 words."""

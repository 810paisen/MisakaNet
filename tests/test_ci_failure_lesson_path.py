#!/usr/bin/env python3
"""The CI-failure → lesson path must be able to see, and must be able to say.

The repository's mechanism for "our CI failed, so surface the lessons that match" is
`ci-lesson-search.yml`, triggered by `Cross-Platform Tests` — the cross-platform matrix, which is
where platform drift shows up. On 2026-10-26 that mechanism fired on a genuinely failing matrix
(measured on rerun attempt 2 of run 40271288490, which reproduced it) and produced **nothing**:
`Get failure context` reported success, both downstream steps were skipped, the pull request got
no note, and the workflow reported green.

Three things had to hold for that, and each of them is pinned here:

* the token has to be allowed to read the job logs (`actions: read`) — reading Actions job logs is
  the capability's whole job, and the token's own permission block printed
  `Contents: read / Metadata: read / PullRequests: write` with no `Actions` at all;
* a log that cannot be read must be **recorded**, not swallowed by an empty `catch` — "we were not
  allowed to look" and "there was nothing to report" have to be distinguishable, because the second
  one used to be reported in the first one's voice;
* a trigger that failed while the capability read nothing must turn the run **red** — a green run
  here reads as "the failure was looked at and needed no lesson", which is the one thing that was
  never established (#1825, and #1984: a green run is not evidence unless something in it can
  differ).

The cross-platform legs stay *soft* gates on purpose — the branch ruleset is named "the
deterministic gates", and a leg whose verdict depends on the runner image's toolchain is not
deterministic (node's test reporter changed its summary prefix in v23.0.0, which is what made the
audit criterion unreadable on macOS on 2026-10-26). So these legs cannot be merge blockers; the
only thing that can make them matter is that their failures *reach* someone. This file is about
that half.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "ci-lesson-search.yml"

# A `catch (e) { … }` block. None of the bodies in this workflow nest braces, so the non-greedy
# body match is enough — and a body that *did* nest braces would show up as a mismatch, not as a
# silent pass.
_CATCH = re.compile(r"catch\s*\([^)]*\)\s*\{([^{}]*)\}")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict:
    return _workflow()["jobs"]["search-lessons"]


def _context_script() -> str:
    """The body of the step that reads the failure."""
    for step in _job()["steps"]:
        if step.get("id") == "context":
            return step["with"]["script"]
    raise AssertionError("ci-lesson-search.yml has no step with id 'context'")


def test_the_path_still_points_at_the_cross_platform_matrix():
    """The trigger is the point: it must react to the matrix that carries the platform drift.

    It must *name* the workflow. `workflows: ["*"]` would start this on every completion of every
    other workflow — 33,317 runs by 2026-09-17, and the trigger is pinned by
    `tests/test_workflow_triggers.py` for the same reason.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'workflows: ["Cross-Platform Tests"]' in text, (
        "the failure→lesson path must react to the cross-platform matrix by name"
    )


def test_the_job_may_read_the_logs_it_exists_to_read():
    """`actions: read`, or the capability cannot read the logs it is here to read.

    Measured 2026-10-26: the token's permission block printed `Contents: read / Metadata: read /
    PullRequests: write` — no `Actions` — and every run of this path produced no signature.
    """
    permissions = _job().get("permissions") or {}
    assert permissions.get("actions") == "read", (
        "reading Actions job logs needs the `actions` scope; without it the read is refused and "
        f"the capability reports itself as silent. permissions: {permissions}"
    )


def test_a_log_that_cannot_be_read_is_recorded_not_swallowed():
    """Every `catch` in the step must keep what it caught.

    The empty `catch (e) { // Logs may not be available }` is what made a refused read look like
    "there was nothing to report" — and a capability that cannot look must say so (#1825).
    """
    body = _context_script()
    for match in _CATCH.finditer(body):
        statements = [
            line for line in match.group(1).splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]
        assert statements, (
            "a swallowed `catch` turns a read that was refused into a search that found nothing; "
            "record it instead"
        )
    assert "readFailures.push" in body, (
        "the step must keep the read failures it collected — that is how 'we could not look' "
        "differs from 'there was nothing to report'"
    )


def test_a_failed_trigger_with_nothing_read_turns_the_run_red():
    """The blind case must not be green.

    A green run here reads as "the CI failure was looked at and needed no lesson". Nothing
    established that, so the run says it is blind instead of staying green.
    """
    body = _context_script()
    assert "core.setFailed(" in body, (
        "a trigger that failed while the capability read nothing must fail the run, not leave it "
        "green — the green run is what made the blind path invisible for months"
    )
    assert "triggerConclusion === 'failure'" in body, (
        "the failure must be tied to the trigger's verdict, not to a guess"
    )


def test_the_run_says_what_it_saw():
    """What the run looked at is written into its log.

    The next reader of a green run has to be able to tell "read nothing because there was nothing"
    from "read nothing because it was refused" without re-running the experiment.
    """
    body = _context_script()
    assert "core.info(" in body, "the step must report what it saw"
    assert "signatures=" in body and "read-failures=" in body, (
        "the summary must name both how many error lines it found and how many reads failed"
    )


def test_the_decision_step_can_reach_a_decision_on_a_failing_log(tmp_path):
    """The bot half of the path must be able to say something about a CI failure.

    Measured 2026-10-26: `scripts/intake_bot.py --log <failing log> --offline --json` answers
    `{"decision": "intake", …}` — so the step that turns signatures into a note has something to
    post, and the comment step's condition (`decision == 'hit' || decision == 'intake'`) can be
    true. `--offline` skips the corpus precheck; without it the same log is checked against the
    corpus first.

    The dedup cache is redirected to a temp directory: a fingerprint is remembered on purpose, so
    the same error on a second run answers `ignore` — and a test that depends on how many times
    this machine has run it is a test that lies.
    """
    log = tmp_path / "ci.log"
    log.write_text(
        "2026-10-26T05:00:48Z FAILED tests/test_gate_mutation_audit.py::"
        "test_audit_passes_for_the_current_gates - AssertionError: hard gates 4/4 covered\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "scripts/intake_bot.py", "--log", str(log), "--offline", "--json"],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
        env={**os.environ, "MISAKA_CACHE_DIR": str(tmp_path / "cache")},
    )
    assert result.returncode == 0, result.stderr
    payload = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
    assert payload is not None, f"intake_bot.py printed no JSON verdict:\n{result.stdout}"
    assert payload.get("decision") in ("hit", "intake"), (
        "the decision step must be able to reach a decision a lesson note can be posted for; "
        f"got {payload.get('decision')!r} — a quiet decision here means the comment step's "
        "condition can never be true"
    )

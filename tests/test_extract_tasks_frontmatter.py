#!/usr/bin/env python3
"""`extract_tasks.py` must read the frontmatter the corpus actually writes.

The defect this exists for
--------------------------
`scripts/extract_tasks.py:extract_frontmatter` matched the `---` fence with a regex and then handed
the inside to `json.loads`. The fence regex is right; the parser is wrong for this corpus. Every
lesson writes **YAML** frontmatter — measured 2026-10-14 over `lessons/`: **516 YAML, 3 JSON, 0
without**. So `json.loads` raised on every YAML file and the function returned `None` through a
bare `except`, silently, for all but three lessons.

Nothing failed and nothing was logged, because the function's contract allows `None`. The damage
only showed up downstream, and only if you looked: a regeneration produced 415 tasks of which
**413 carried `domain: "general"`** regardless of what each lesson's frontmatter said, collapsing
a 9-domain working set to 3.

Why this is a generator bug and not a curation decision
------------------------------------------------------
It is tempting to read "the snapshot has 98 files and the corpus yields 441" as a stale snapshot
needing a refresh. It is not, and this test is what settles it: the generator was not producing
415 *lessons*, it was producing 413 near-identical `general` rows. The corpus was never the
bottleneck — the parser was.

The fix delegates to `scripts/lesson_gate.parse_frontmatter`, which already handles JSON, YAML and
the JSON+provenance legacy quirk, and returns the `(dict, body)` pair this call site wants. It is
the sixth frontmatter parser in this repository; this change adds none.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def et():
    spec = importlib.util.spec_from_file_location(
        "extract_tasks_under_test", REPO / "scripts" / "extract_tasks.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "lesson-probe.md"
    path.write_text(body, encoding="utf-8")
    return path


# A minimal but real lesson: both sections the extractor requires, so a failure below is about the
# frontmatter and not about the body.
BODY = "\n## Problem\n\n调用第三方 API 时返回 HTTP 429。\n\n## Solution\n\n指数退避重试。\n"


def test_yaml_frontmatter_is_read_not_swallowed(et, tmp_path):
    """The shape that used to fail: valid YAML between the fences, unparseable as JSON."""
    path = _write(tmp_path, f'---\ntitle: Rate Limit Handling\ndomain: devops\n---\n{BODY}')
    fm = et.extract_frontmatter(path)
    assert fm is not None, "YAML frontmatter returned None — the json.loads parser is back"
    assert fm["domain"] == "devops"
    assert fm["title"] == "Rate Limit Handling"


def test_json_frontmatter_still_works(et, tmp_path):
    """The three legacy files must not regress onto the YAML-only path."""
    path = _write(tmp_path, '---\n{"title": "Legacy", "domain": "rag"}\n---\n' + BODY)
    fm = et.extract_frontmatter(path)
    assert fm is not None and fm["domain"] == "rag"


def test_a_lesson_with_no_frontmatter_is_not_an_error(et, tmp_path):
    """`None` is a legitimate answer here — the extractor falls back to the filename."""
    path = _write(tmp_path, BODY)
    assert et.extract_frontmatter(path) is None


def test_a_real_corpus_lesson_keeps_its_domain(et):
    """The end-to-end claim: a real lesson's domain survives extraction.

    Pinned on the real corpus rather than a fixture so that a change to the *corpus's* frontmatter
    convention shows up here instead of turning the 415-task regeneration into 413 `general` rows
    again. `lessons/contrib/api-rate-limit-handling.md` is YAML and declares `devops`.
    """
    task = et.lesson_to_task(REPO / "lessons" / "contrib" / "api-rate-limit-handling.md")
    assert task is not None, "the pinned lesson stopped being extractable — pick another fixture"
    assert task["domain"] == "devops", (
        f"expected the lesson's own domain, got {task['domain']!r}. A regeneration run now "
        "labels nearly everything 'general' when the frontmatter is not being read."
    )


def test_the_extraction_uses_real_domains_across_the_corpus(et):
    """The measured shape, so the regression is visible as a distribution rather than one file.

    Threshold is deliberately loose — this is a "did it collapse to one bucket" check, not a count.
    """
    domains = set()
    for path in sorted(et.LESSONS_DIR.rglob("*.md")):
        if path.name in ("index.md", "README.md") or "_archive" in str(path):
            continue
        task = et.lesson_to_task(path)
        if task:
            domains.add(task["domain"])
    assert len(domains) >= 20, (
        f"only {len(domains)} distinct domains across the corpus: {sorted(domains)}. With the "
        "json.loads parser this collapses to 1 ('general')."
    )
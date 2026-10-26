#!/usr/bin/env python3
"""A site page may not call a helper it does not define — and this file is that rule's proof.

**The failure it exists for.** On 2026-10-22 the homepage's network-activity panel told the reader
"活动快照暂不可用" — "the activity snapshot is unavailable" — while `/api/activity` was answering
200. The API was never the problem. #3694 moved the registration form to `/register/` and, in
moving it, deleted the two helpers that sat beside it in the homepage's script:

* `escapeHTML(val)` — used twelve times by the panels that render JSON into markup;
* `readJsonResponse(response)` — the reader every fetch on the page ended in, used by
  `fetchWithTimeout` and `fetchPOST`.

The form was the only thing that used them *as a form*, and the homepage's own panels kept calling
them. A classic script resolves an undefined name **at the call**, not at the load, so the page
loaded cleanly and every panel threw a `ReferenceError` the moment it had data to render. Each
panel's own `catch` then turned that into the page's "unavailable" wording, which is a claim about
the site's age and was false. Three panels went blank at once and the API was fine.

Nothing in the repository could see it. There is no eslint, no bundler and no type checker in the
tree; the one JS test covers `docs/js/core.js`, a module the homepage does not load. The name was
called twelve times and defined zero times, and every gate was green.

**Why a scope check and not a linter.** The question the browser asks at runtime is "does this name
exist here", so that is the question asked here: the page's own definitions, the definitions of
the scripts it loads, and the standard library. Types, shadowing and `this` are somebody else's
gate, and a gate that cannot be satisfied gets deleted rather than fixed.

**What the gate must not do.** It must not cry wolf. A false positive on a page that is fine would
teach every maintainer to ignore it, and then it would be worse than no gate at all — so the
tests below pin the two things that make it trustworthy: that it goes red on the shape #3694 left
behind, and that it stays quiet on the shapes that are fine (a standard global, a regular
expression that contains a quote, a division).
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_page_scripts", REPO / "scripts" / "check_page_scripts.py"
)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

SITE = REPO / "docs"
HOME = SITE / "index.html"
REGISTER = SITE / "register" / "index.html"


@pytest.fixture(scope="module")
def findings() -> list[dict]:
    return checker.scan(SITE)


def test_no_page_calls_a_helper_it_does_not_define(findings):
    """The gate itself: every page resolves every name it calls."""
    assert not findings, "\n".join(str(f["message"]) for f in findings)


def test_the_gate_goes_red_on_the_shape_3694_left_behind(tmp_path):
    """A gate that cannot go red is not a gate.

    The page below is the homepage reduced to the two facts that mattered: it calls a helper, and
    it does not define it. The check has to say so.
    """
    page = tmp_path / "index.html"
    page.write_text(
        "<html><body><script>\n"
        "function panel() { return escapeHTML('x'); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    found = checker.scan(tmp_path)
    assert [f["name"] for f in found] == ["escapeHTML"], found
    assert "index.html" in str(found[0]["page"]), found


def test_the_gate_goes_red_on_a_missing_json_reader(tmp_path):
    """The second half of #3694, which the panel's `catch` hid behind the first."""
    page = tmp_path / "index.html"
    page.write_text(
        "<html><body><script>\n"
        "async function fetchWithTimeout(url) { return readJsonResponse(await fetch(url)); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    found = checker.scan(tmp_path)
    assert [f["name"] for f in found] == ["readJsonResponse"], found


def test_a_standard_global_is_not_a_missing_helper(tmp_path):
    """`alert` is a global: flagging it would be a false positive, and false positives teach
    people to ignore the gate."""
    page = tmp_path / "index.html"
    page.write_text(
        "<html><body><script>\n"
        "function panel() { alert('hi'); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    assert checker.scan(tmp_path) == []


def test_a_helper_defined_in_the_page_is_not_reported(tmp_path):
    """The other half of the contract: a page that defines what it calls is left alone."""
    page = tmp_path / "index.html"
    page.write_text(
        "<html><body><script>\n"
        "function escapeHTML(val) { return val; }\n"
        "function panel() { return escapeHTML('x'); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    assert checker.scan(tmp_path) == []


def test_a_regular_expression_containing_a_quote_does_not_eat_the_file():
    """The reason this file has a tokenizer instead of a regular expression.

    `replace(/[&<>"']/g, …)` is a regular expression that contains a quote. A scanner that treats
    that quote as the start of a string loses the rest of the file — and with it every definition
    after it, so the page looks like it calls a dozen helpers it never defines.
    """
    tokens = checker.tokenize("function f(v) { return v.replace(/[&<>\"']/g, c => c); }\n")
    assert ("ident", "replace") in tokens
    assert ("regex", "") in tokens
    # The `'` inside the regular expression did not open a string: the function's own name and
    # the arrow parameter are both still identifiers.
    assert ("ident", "f") in tokens
    assert ("ident", "c") in tokens


def test_a_division_is_not_read_as_a_regular_expression():
    """The other direction: `a / b` divides, and the `b` is still a name the file uses."""
    tokens = checker.tokenize("const ratio = total / count;\n")
    assert ("regex", "") not in tokens
    assert ("ident", "count") in tokens


@pytest.mark.parametrize("end_tag", [
    "</script>",
    "</script >",
    "</script/>",
    '</script foo="bar">',
])
def test_a_script_end_tag_may_carry_whitespace(tmp_path, end_tag):
    """The end tag is not spelled one way, and a pattern that misses it loses the whole script.

    The HTML syntax allows whitespace -- and a solidus -- between the tag name and the `>`, and a
    browser accepts an end tag carrying attributes as well (`</script foo="bar">`), even though
    that is a parse error. A pattern that stops at the literal `</script>` does not merely reject
    those: with a single script on the page it matches **nothing at all**, and a page whose
    script is invisible is a page whose calls are all reported as missing. CodeQL flagged the
    same pattern, and it was right.
    """
    page = tmp_path / "index.html"
    page.write_text(
        "<html><body><script>\n"
        "function panel() { return escapeHTML('x'); }\n"
        f"{end_tag}\n"
        "</body></html>\n",
        encoding="utf-8",
    )
    found = checker.scan(tmp_path)
    assert [f["name"] for f in found] == ["escapeHTML"], found


def test_the_homepage_loads_the_helpers_its_panels_call():
    """The regression, stated in the plainest words available.

    The panel renderers below are the reason the two helpers exist at all: `safeHref` escapes a
    URL, and the footnote, the feed, the history bars and the contributor wall all escape text
    they interpolate. A page that renders untrusted values needs an escaper.

    They live in `docs/js/site.js` now — one copy for the whole site — and the homepage loads it
    before the script that calls them. The copy that used to sit in the page was the copy #3694
    deleted, and the panels that called it went blank while the API was answering 200.
    """
    html = HOME.read_text(encoding="utf-8")
    assert 'src="/js/site.js"' in html, (
        "the homepage renders JSON into markup and no longer loads the escaper it calls"
    )
    # The page must not keep a copy of its own: two copies are how the copies drifted apart.
    assert "function escapeHTML(val)" not in html, (
        "the homepage still carries its own copy of the escaper — that is the shape the shared "
        "module exists to end"
    )
    assert "function readJsonResponse(response)" not in html, (
        "the homepage still carries its own copy of the JSON reader"
    )
    module = (SITE / "js" / "site.js").read_text(encoding="utf-8")
    assert "function escapeHTML(val)" in module, "the shared module must provide the escaper"
    assert "async function readJsonResponse(response)" in module, (
        "the shared module must provide the JSON reader"
    )


def test_a_page_that_keeps_its_own_copy_of_a_shared_helper_is_reported(tmp_path):
    """The second half of the rule: **one** copy, not one per page.

    The seven copies are how they drifted apart: four escaped with `String(value)` (a null
    printed as the words "null"), one with `String(value || '')` (a count of zero printed as
    nothing), and two with the `??` form. The gate below says so out loud, so the next copy is a
    finding rather than a silent divergence.
    """
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "site.js").write_text(
        "function escapeHTML(val) { return val; }\n", encoding="utf-8")
    (tmp_path / "one.html").write_text(
        "<html><body><script>\n"
        "function escapeHTML(val) { return val; }\n"
        "function panel() { return escapeHTML('x'); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    found = checker.scan(tmp_path)
    assert [f["kind"] for f in found] == ["duplicate"], found
    assert [f["name"] for f in found] == ["escapeHTML"], found


def test_a_page_that_loads_the_shared_module_is_not_reported(tmp_path):
    """The other half: loading the module is how a page gets the helper, and it is not a finding.

    The `src` is absolute (`/js/site.js`), the way the site links to itself everywhere else, so
    the checker has to resolve it against the **site root** rather than against the page's own
    directory — the browser resolves it the same way.
    """
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "site.js").write_text(
        "function escapeHTML(val) { return val; }\n", encoding="utf-8")
    (tmp_path / "two.html").write_text(
        '<html><body><script src="/js/site.js"></script>\n'
        "<script>\n"
        "function panel() { return escapeHTML('x'); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    assert checker.scan(tmp_path) == []


def test_a_local_shadow_of_a_shared_helper_is_not_a_second_copy(tmp_path):
    """The false-positive direction, which is the one that matters.

    A page that declares `escapeHTML` **inside a function** is shadowing the shared escaper for
    that function — it is not keeping a second copy of it. A gate that said otherwise would be
    wrong about ordinary code, and a gate that is wrong about ordinary code teaches every
    maintainer to ignore it.
    """
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "site.js").write_text(
        "function escapeHTML(val) { return val; }\n", encoding="utf-8")
    (tmp_path / "one.html").write_text(
        "<html><body><script>\n"
        "function panel() { const escapeHTML = (v) => v; return escapeHTML('x'); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    assert checker.scan(tmp_path) == []


def test_the_reverse_test_covers_both_kinds_of_finding():
    """`scripts/check_page_scripts.py --reverse-test` is the proof that the gate can go red.

    It has two legs — a name called and never defined, and a second copy of a shared helper —
    and it has to keep both: a gate that can only go red one way is a gate whose green is
    half a claim.
    """
    assert checker._reverse_test(REPO / "docs") == 0


def test_a_module_loaded_as_a_module_does_not_provide_the_helper(tmp_path):
    """A module's scope is private, and a module script is deferred.

    Loading the shared module as `<script type="module" src="/js/site.js">` would leave every page
    that calls it with an undefined name — the #3694 shape, with the module still sitting on disk
    and the file still there for a reader to believe in. Nothing else in the repository would say
    so, so the gate has to.
    """
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "site.js").write_text(
        "function escapeHTML(val) { return val; }\n", encoding="utf-8")
    (tmp_path / "two.html").write_text(
        '<html><body><script type="module" src="/js/site.js"></script>\n'
        "<script>\n"
        "function panel() { return escapeHTML('x'); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    found = checker.scan(tmp_path)
    assert [f["name"] for f in found] == ["escapeHTML"], found
    assert [f["kind"] for f in found] == ["missing"], found


def test_a_loaded_scripts_local_names_are_not_the_pages_names(tmp_path):
    """A classic script's top-level declarations reach the next script; a function's locals do not.

    The scope rule is the browser's, and getting it wrong in the permissive direction hides a
    real bug: a page calling a name that only exists inside another script's function throws a
    `ReferenceError` the first time that line runs.
    """
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "site.js").write_text(
        "function helper() { const localOnly = 1; return localOnly; }\n", encoding="utf-8")
    (tmp_path / "two.html").write_text(
        '<html><body><script src="/js/site.js"></script>\n'
        "<script>\n"
        "function panel() { return localOnly(); }\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    found = checker.scan(tmp_path)
    assert [f["name"] for f in found] == ["localOnly"], found


def test_the_shared_module_is_the_one_the_pages_load():
    """The copy that is left is the copy every page uses.

    The pages and the module are two files, so they can drift apart again — the way the seven
    copies did. This pins that the module still is what the pages load, and that it still holds
    the canonical escape: `??` and not `||`, because `||` turns a count of zero into nothing.
    """
    module = (SITE / "js" / "site.js").read_text(encoding="utf-8")
    assert "String(val ?? '')" in module, (
        "the canonical escape must keep the `??`: `||` turns a zero into an empty string"
    )
    for page in (HOME, REGISTER, SITE / "search" / "index.html"):
        assert 'src="/js/site.js"' in page.read_text(encoding="utf-8"), (
            f"{page.name} must load the shared module rather than keep a copy"
        )


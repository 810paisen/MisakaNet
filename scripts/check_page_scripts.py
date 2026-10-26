#!/usr/bin/env python3
"""A site page may not call a helper it does not define.

**What this catches.** The homepage stopped rendering its network-activity panel on 2026-10-22, and
the reason was not the API: `/api/activity` was answering 200 the whole time. #3694 moved the
registration form to `/register/` and, in doing so, deleted the `function escapeHTML(val) { … }`
that lived beside it — the form was the only thing on the homepage that used it. Four other panels
kept calling it. A classic script resolves an undefined name **at the call**, not at the load, so
the page loaded, the panel tried to render its footnote, and a `ReferenceError` was thrown from
inside the render. The panel's own `catch` then fell back to the snapshot, whose fallback threw in
the same place, and the reader was told "活动快照暂不可用" — a claim about the *site's age* that was
a lie. Three panels (activity, latest updates, contributor wall) went blank or degraded at once,
and the API was fine.

Nothing in the repository could see it. The page is one HTML file with one inline `<script>`; there
is no eslint, no bundler, and no type checker in the tree (`ls .eslintrc* eslint.config*` finds
nothing), and the one JS test that exists covers `docs/js/core.js`, a module this page does not
load. The name was used twelve times and defined zero times, and every gate was green.

**The rule.** A page's inline script may call a name only if the page itself defines it, a script
the page loads defines it, or it is a standard JavaScript / browser global. Anything else is a
reference that will throw the first time that line runs — and a line that only runs when a panel
has data is exactly the line nobody tests.

This is a *scope* check, not a type check: it answers "does this name exist here", which is the
question the browser answers with a `ReferenceError` at runtime. It is deliberately not trying to
be a linter — it does not check types, shadowing, or `this`.

**How it reads the pages.** The pages are hand-written vanilla JavaScript in HTML, so the analysis
needs a JavaScript *tokenizer*, not a regular expression: `/[&<>"']/g` is a regular expression
containing a quote, and a scanner that treats that quote as the start of a string loses the rest of
the file. The tokenizer here tracks the previous significant token to tell a regular expression from
a division, which is the same rule the ECMAScript specification gives.

**Usage:**
    python3 scripts/check_page_scripts.py            # report findings
    python3 scripts/check_page_scripts.py --check    # non-zero exit if a page is broken
    python3 scripts/check_page_scripts.py --json     # machine-readable findings
    python3 scripts/check_page_scripts.py --reverse-test  # prove the check can go red
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_ROOT = REPO_ROOT / "docs"

# Standard JavaScript and browser globals. A name in this list is available everywhere without
# being defined; a name outside it has to come from the page. The list is the only place a false
# positive can be silenced, so it is written out rather than inferred.
BROWSER_GLOBALS = {
    # ECMAScript built-ins
    "Array", "ArrayBuffer", "ArrayBufferView", "BigInt", "BigInt64Array", "BigUint64Array",
    "Boolean", "DataView", "Date", "decodeURI", "decodeURIComponent", "encodeURI",
    "encodeURIComponent", "Error", "escape", "eval", "EvalError", "FinalizationRegistry",
    "Float32Array", "Float64Array", "Function", "globalThis", "Infinity", "Int8Array", "Int16Array",
    "Int32Array", "Intl", "isFinite", "isNaN", "JSON", "Map", "Math", "NaN", "Number", "Object",
    "parseFloat", "parseInt", "Promise", "Proxy", "RangeError", "ReferenceError", "Reflect",
    "RegExp", "Set", "SharedArrayBuffer", "String", "Symbol", "SyntaxError", "TypeError",
    "Uint8Array", "Uint8ClampedArray", "Uint16Array", "Uint32Array", "undefined", "unescape",
    "URIError", "WeakMap", "WeakRef", "WeakSet",
    # browser host objects
    "AbortController", "AbortSignal", "addEventListener", "alert", "Animation", "atob", "Audio",
    "Blob", "btoa", "blur", "BroadcastChannel", "CanvasRenderingContext2D", "clearInterval",
    "clearTimeout", "close", "confirm", "console", "CryptoKey", "CustomElementRegistry",
    "CustomEvent", "devicePixelRatio", "document", "DOMParser", "Element", "Event", "EventTarget",
    "fetch", "File", "FileReader", "focus", "FormData", "frames", "getComputedStyle", "Headers",
    "history", "Image", "IntersectionObserver", "localStorage", "location", "matchMedia",
    "MessageChannel", "MessagePort", "moveBy", "moveTo", "MutationObserver", "navigator", "Node",
    "NodeList", "Notification", "open", "parent", "Path2D", "performance", "postMessage", "print",
    "prompt", "PromiseRejectionEvent", "queueMicrotask", "requestAnimationFrame",
    "cancelAnimationFrame", "requestIdleCallback", "requestSubmit", "resizeBy", "resizeTo",
    "ResizeObserver", "Response", "screen", "scroll", "scrollBy", "scrollTo", "self",
    "sessionStorage", "setInterval", "setTimeout", "ShadowRoot", "SpeechSynthesisUtterance",
    "stop", "structuredClone", "Text", "TextDecoder", "TextEncoder", "top", "trustedTypes",
    "URL", "URLSearchParams", "window", "Worker", "XMLHttpRequest", "WebSocket",
}

# JavaScript keywords are never a call to a helper, so they are not reported as missing.
KEYWORDS = {
    "await", "break", "case", "catch", "class", "const", "continue", "debugger", "default",
    "delete", "do", "else", "export", "extends", "finally", "for", "function", "if", "import",
    "in", "instanceof", "let", "new", "of", "return", "static", "super", "switch", "this",
    "throw", "try", "typeof", "var", "void", "while", "with", "yield", "async", "get", "set",
}

# After these tokens a `/` opens a regular expression rather than dividing.
_REGEX_AFTER_PUNCT = set("([{=,:;!&|?+-*%^~<>")
_REGEX_AFTER_KEYWORD = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case",
    "do", "else", "yield", "await",
}

_IDENT_START = re.compile(r"[A-Za-z_$]")
_IDENT_BODY = re.compile(r"[A-Za-z0-9_$]")

# `<script>` tags whose body is not JavaScript.
_NON_JS_TYPES = {
    "application/ld+json", "application/json", "text/template", "text/html", "text/x-template",
}


def _regex_allowed(prev: tuple[str, str] | None) -> bool:
    """Whether a `/` at this position starts a regular expression literal.

    The rule is the specification's: a `/` divides when the previous token could end an
    expression (a name, a number, a string, `)`, `]`, `++`, `--`), and starts a regular
    expression otherwise.
    """
    if prev is None:
        return True
    kind, value = prev
    if kind == "punct":
        if value in (")", "]", "++", "--"):
            return False
        return value in _REGEX_AFTER_PUNCT
    if kind == "ident":
        return value in _REGEX_AFTER_KEYWORD
    return False  # number, string, regular expression


def tokenize(src: str) -> list[tuple[str, str]]:
    """Tokenize JavaScript into ``(kind, text)`` pairs.

    Kinds: ``ident``, ``num``, ``str``, ``regex``, ``punct``. Comments are dropped. String and
    regular-expression *contents* are dropped as well, so a quote inside a regular expression
    cannot end a string that was never started.
    """
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(src)
    prev: tuple[str, str] | None = None
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in "\"'`":
            quote = c
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            tokens.append(("str", ""))
            prev = ("str", "")
            continue
        if c == "/" and _regex_allowed(prev):
            i += 1
            in_class = False
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                elif src[i] == "/" and not in_class:
                    i += 1
                    break
                elif src[i] == "\n":
                    break
                i += 1
            while i < n and _IDENT_BODY.match(src[i]):
                i += 1
            tokens.append(("regex", ""))
            prev = ("regex", "")
            continue
        if _IDENT_START.match(c):
            j = i + 1
            while j < n and _IDENT_BODY.match(src[j]):
                j += 1
            name = src[i:j]
            i = j
            tokens.append(("ident", name))
            prev = ("ident", name)
            continue
        if c.isdigit():
            j = i
            while j < n and (src[j].isalnum() or src[j] in "._"):
                j += 1
            i = j
            tokens.append(("num", ""))
            prev = ("num", "")
            continue
        for op in ("===", "!==", "=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--",
                   "+=", "-=", "*=", "/=", "**", "<<", ">>"):
            if src.startswith(op, i):
                i += len(op)
                tokens.append(("punct", op))
                prev = ("punct", op)
                break
        else:
            i += 1
            tokens.append(("punct", c))
            prev = ("punct", c)
    return tokens


def _matching_paren(tokens: list[tuple[str, str]], start: int) -> int:
    """Index of the ``)`` that closes the ``(`` at ``start``, or -1."""
    depth = 0
    for j in range(start, len(tokens)):
        if tokens[j] == ("punct", "("):
            depth += 1
        elif tokens[j] == ("punct", ")"):
            depth -= 1
            if depth == 0:
                return j
    return -1


def declared_names(tokens: list[tuple[str, str]]) -> set[str]:
    """Every name the script introduces: declarations, parameters, and shorthand methods."""
    names: set[str] = set()
    n = len(tokens)

    i = 0
    while i < n:
        kind, text = tokens[i]
        if kind != "ident":
            i += 1
            continue
        if text in ("function", "class") and i + 1 < n and tokens[i + 1][0] == "ident":
            names.add(tokens[i + 1][1])
            i += 2
            continue
        if text in ("const", "let", "var"):
            j = i + 1
            if j < n and tokens[j][0] == "ident":
                names.add(tokens[j][1])
            elif j < n and tokens[j] == ("punct", "{"):
                for k in range(j + 1, n):
                    if tokens[k] == ("punct", "}"):
                        break
                    if tokens[k][0] == "ident":
                        names.add(tokens[k][1])
            elif j < n and tokens[j] == ("punct", "["):
                for k in range(j + 1, n):
                    if tokens[k] == ("punct", "]"):
                        break
                    if tokens[k][0] == "ident":
                        names.add(tokens[k][1])
            i += 1
            continue
        if text == "import" and i + 1 < n and tokens[i + 1][0] == "ident":
            j = i + 1
            while j < n and tokens[j] != ("punct", ";"):
                if tokens[j][0] == "ident" and tokens[j][1] not in ("from", "as"):
                    names.add(tokens[j][1])
                j += 1
            i = j
            continue
        if text == "catch" and i + 1 < n and tokens[i + 1] == ("punct", "("):
            if i + 2 < n and tokens[i + 2][0] == "ident":
                names.add(tokens[i + 2][1])
            i += 1
            continue
        # `window.foo = …` publishes a global at runtime.
        if (i + 3 < n and tokens[i + 1] == ("punct", ".")
                and tokens[i + 2][0] == "ident" and tokens[i + 3] == ("punct", "=")):
            names.add(tokens[i + 2][1])
        i += 1

    # Parameters and object/class shorthand methods.
    for i, (kind, text) in enumerate(tokens):
        if kind != "punct" or text != "(":
            continue
        close = _matching_paren(tokens, i)
        if close == -1:
            continue
        after = tokens[close + 1] if close + 1 < n else None
        before = tokens[i - 1] if i > 0 else None
        is_params = after is not None and after[0] == "punct" and after[1] in ("=>", "{")
        is_shorthand = (
            after is not None and after[0] == "punct" and after[1] == "{"
            and before is not None
            and (before[0] == "ident"
                 or (before[0] == "punct" and before[1] not in (")", "]", "}")))
            and not (before[0] == "ident"
                     and before[1] in ("if", "for", "while", "switch", "catch"))
        )
        if not (is_params or is_shorthand):
            continue
        k = i + 1
        while k < close:
            if tokens[k][0] == "ident":
                if k + 1 < n and tokens[k + 1] == ("punct", "."):
                    k += 3
                    continue
                names.add(tokens[k][1])
            k += 1
    return names


def called_names(tokens: list[tuple[str, str]]) -> set[str]:
    """Every name the script calls: an identifier immediately followed by ``(``."""
    names: set[str] = set()
    for i, (kind, text) in enumerate(tokens):
        if kind != "ident" or text in KEYWORDS:
            continue
        if i + 1 >= len(tokens) or tokens[i + 1] != ("punct", "("):
            continue
        prev = tokens[i - 1] if i > 0 else None
        if prev and prev[0] == "punct" and prev[1] in (".", "?."):
            continue  # a method on an object, not a global
        if prev and prev[0] == "ident" and prev[1] in ("function", "class"):
            continue  # the name being declared, not called
        names.add(text)
    return names


def script_sources(html: str) -> list[str]:
    """The JavaScript a page carries inline.

    ``<script src=…>`` is skipped here (its contents are another file), and a ``type`` that is not
    JavaScript is skipped as well — JSON-LD blocks are data, and their braces are not code.

    The end tag is matched as ``</script`` followed by anything up to the closing ``>``, because
    that is what the HTML syntax allows: ``</script >``, ``</script/>`` and even
    ``</script foo="bar">`` are all end tags a browser accepts, and a pattern that stops at the
    literal ``</script>`` silently runs two script bodies together when a page writes one of
    them -- and with a single script on the page it matches nothing at all, so the page's calls
    are all reported as missing. CodeQL says the same thing, and it is right: the pattern was
    incomplete, not merely strict.
    """
    bodies: list[str] = []
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script[^>]*>", html, re.S | re.I):
        attrs = match.group(1) or ""
        body = match.group(2) or ""
        if re.search(r"\bsrc\s*=", attrs, re.I):
            continue
        type_match = re.search(r"\btype\s*=\s*[\"']([^\"']*)[\"']", attrs, re.I)
        if type_match and type_match.group(1).strip().lower() not in (
            "", "text/javascript", "application/javascript", "module", "text/ecmascript",
            "application/ecmascript",
        ):
            continue
        bodies.append(body)
    return bodies


def _local_script_names(html: str, page: Path, site_root: Path) -> set[str]:
    """Names defined by scripts this page loads.

    A `src` is resolved the way a browser resolves it: a relative one against the page's own
    directory, an absolute one (``/js/site.js``) against the site's root — which is `docs/`, the
    directory the site is served from.

    A loaded **script file** is code, not markup: ``script_sources()`` looks for ``<script>``
    blocks, so it finds nothing in a ``.js`` file and every helper the page loads would read as
    missing. The shared module (`docs/js/site.js`) is exactly that case, so a ``.js`` file is
    tokenized as the JavaScript it is.

    Two scope rules, both of them the browser's:

    * **Top level only.** A classic script's top-level declarations reach the next script; a name
      declared inside a function does not. So a loaded script contributes its top-level names and
      nothing else -- the same reason the "second copy" rule looks at top level only.
    * **A module contributes nothing.** A module's scope is private to it, and a module script is
      deferred, so it runs *after* the page's own classic scripts. Loading the shared module as a
      module would leave every page that calls it with an undefined name -- the #3694 shape, with
      the module still present on disk.
    """
    names: set[str] = set()
    for match in re.finditer(r"<script\b([^>]*)>", html, re.I):
        attrs = match.group(1) or ""
        src_match = re.search(r"\bsrc\s*=\s*[\"']([^\"']+)[\"']", attrs, re.I)
        if not src_match:
            continue
        type_match = re.search(r"\btype\s*=\s*[\"']([^\"']*)[\"']", attrs, re.I)
        if type_match:
            script_type = type_match.group(1).strip().lower()
            # "module" is deliberately absent from this list: a module's scope is private to it,
            # and a module script is deferred, so it runs *after* the page's own classic scripts.
            if script_type not in ("", "text/javascript", "application/javascript",
                                   "text/ecmascript", "application/ecmascript"):
                continue
        src = src_match.group(1)
        if re.match(r"^(https?:)?//", src) or src.startswith("data:"):
            continue
        clean = src.split("?")[0].split("#")[0]
        path = (site_root / clean.lstrip("/") if clean.startswith("/")
                else page.parent / clean).resolve()
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in (".js", ".mjs", ".cjs"):
            tokens = tokenize(text)
        else:
            tokens = []
            for body in script_sources(text):
                tokens.extend(tokenize(body))
        names |= top_level_names(tokens)
    return names


def top_level_names(tokens: list[tuple[str, str]]) -> set[str]:
    """The names a script declares at its top level.

    Not `declared_names()`: that one also collects local variables and parameters, and a shared
    module's *local* names are none of the page's business — `text` and `val` are not API.

    Top level is the point. A name a page declares inside a function is that function's own
    business: a page with `function card(x) { const escapeHTML = …; }` is shadowing the shared
    escaper locally, not keeping a second copy of it, and telling that page it is wrong would be
    a false positive — a false positive teaches every maintainer to ignore the gate, which is
    worse than having no gate.
    """
    names: set[str] = set()
    depth = 0
    n = len(tokens)
    for i, (kind, text) in enumerate(tokens):
        if kind == "punct":
            if text in ("(", "[", "{"):
                depth += 1
            elif text in (")", "]", "}"):
                depth -= 1
            continue
        if kind != "ident" or depth != 0:
            continue
        if text in ("function", "class"):
            j = i + 1
            if j < n and tokens[j] == ("punct", "*"):
                j += 1
            if j < n and tokens[j][0] == "ident":
                names.add(tokens[j][1])
            continue
        if text in ("const", "let", "var"):
            j = i + 1
            if j < n and tokens[j][0] == "ident":
                names.add(tokens[j][1])
            elif j < n and tokens[j] == ("punct", "{"):
                names |= _destructured_names(tokens, j, "}")
            elif j < n and tokens[j] == ("punct", "["):
                names |= _destructured_names(tokens, j, "]")
            continue
        # `window.foo = …` publishes a global at runtime.
        if (i + 3 < n and tokens[i + 1] == ("punct", ".")
                and tokens[i + 2][0] == "ident" and tokens[i + 3] == ("punct", "=")):
            names.add(tokens[i + 2][1])
    return names


def _destructured_names(tokens: list[tuple[str, str]], open_at: int, closer: str) -> set[str]:
    """The names bound by a destructuring pattern: `const { a, b } = …`, `const [ x ] = …`."""
    names: set[str] = set()
    for k in range(open_at + 1, len(tokens)):
        if tokens[k] == ("punct", closer):
            break
        if tokens[k][0] == "ident":
            names.add(tokens[k][1])
    return names


def shared_helper_names(site_root: Path) -> set[str]:
    """The names the shared module provides.

    ``docs/js/site.js`` is the one copy of the rules that used to be copied per page. A page that
    defines one of them itself has a second copy, and a second copy is how the copies drifted
    apart in the first place: four pages escaped with ``String(value)`` (a null printed as the
    words "null"), one with ``String(value || '')`` (a count of zero printed as nothing), and two
    with ``String(val ?? '')``.
    """
    module = site_root / "js" / "site.js"
    if not module.is_file():
        return set()
    return top_level_names(tokenize(module.read_text(encoding="utf-8", errors="replace")))


def page_names(page: Path) -> tuple[set[str], set[str], set[str]]:
    """``(defined, called, top_level)`` for one page's inline script.

    ``defined`` is every name the script introduces, ``called`` is every name it calls, and
    ``top_level`` is the subset of ``defined`` that the script publishes to the page's scope —
    the third one is what the "second copy" rule compares against the shared module.
    """
    html = page.read_text(encoding="utf-8", errors="replace")
    tokens: list[tuple[str, str]] = []
    for body in script_sources(html):
        tokens.extend(tokenize(body))
    return declared_names(tokens), called_names(tokens), top_level_names(tokens)


def scan(site_root: Path) -> list[dict[str, object]]:
    """Find every page that calls a name it does not define — and every page that keeps its own
    copy of a name the shared module already provides.

    Returns one finding per ``(page, name)`` pair, sorted.
    """
    pages = sorted(site_root.rglob("*.html"))
    profiles: dict[Path, tuple[set[str], set[str], set[str]]] = {}
    for page in pages:
        profiles[page] = page_names(page)

    shared = shared_helper_names(site_root)
    findings: list[dict[str, object]] = []
    for page, (defined, called, top_level) in profiles.items():
        available = defined | _local_script_names(
            page.read_text(encoding="utf-8", errors="replace"), page, site_root
        ) | BROWSER_GLOBALS
        for name in sorted(called - available):
            relative = str(page.relative_to(site_root.parent)) if site_root.parent in page.parents \
                else str(page)
            findings.append({
                "page": relative,
                "name": name,
                "kind": "missing",
                "message": f"{relative} calls `{name}(...)` but never defines it",
            })
        # A second copy of a shared helper is the shape the copies drifted apart in, and it is
        # how the homepage lost the escaper in #3694: the copy that was deleted was the copy the
        # panels needed. The shared module is the one place a rule like this lives.
        #
        # `top_level`, not `defined`: a name a page declares *inside* a function is that
        # function's own business, and flagging it would be a false positive.
        for name in sorted(top_level & shared):
            relative = str(page.relative_to(site_root.parent)) if site_root.parent in page.parents \
                else str(page)
            findings.append({
                "page": relative,
                "name": name,
                "kind": "duplicate",
                "message": f"{relative} defines its own `{name}(...)`; "
                           f"the shared module /js/site.js already provides it",
            })
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="non-zero exit if a page is broken")
    parser.add_argument("--json", action="store_true", help="machine-readable findings")
    parser.add_argument("--site-root", type=Path, default=SITE_ROOT, help="the pages to scan")
    parser.add_argument("--reverse-test", action="store_true",
                        help="prove the check can go red on a page with a missing helper")
    args = parser.parse_args(argv)

    site_root = args.site_root.resolve()

    if args.reverse_test:
        return _reverse_test(site_root)

    findings = scan(site_root)
    if args.json:
        print(json.dumps(findings, indent=2, ensure_ascii=False))
        return 1 if (args.check and findings) else 0

    if not findings:
        pages = len(list(site_root.rglob("*.html")))
        print(f"OK: no page calls a helper it does not define ({pages} pages scanned)")
        return 0
    for finding in findings:
        print(f"ERROR: {finding['message']}")
    print(f"\n{len(findings)} unresolved call(s). A page that calls a name it never defines throws "
          f"a ReferenceError the first time that line runs.")
    return 1 if args.check else 0


def _reverse_test(site_root: Path) -> int:
    """Prove the check can go red — on both kinds of finding.

    A gate that cannot go red is not a gate, so this is part of the script rather than a note
    about it. Two shapes are injected, both of them the shapes the site has actually been in:

    * **missing** — the shape #3694 left behind: a page that calls `escapeHTML` without defining
      it. The page that *does* define it must stay quiet, or the gate cries wolf.
    * **duplicate** — the shape that produced the seven drifting copies: a page that keeps its
      own copy of a name the shared module already provides. The page that *loads* the module
      must stay quiet, and so must the page that shadows the name inside a function, because
      that is a local business and not a second copy.

    The duplicate leg copies the **real** shared module into the temporary site rather than a
    hand-written stub, so the leg tests the module the site actually ships.
    """
    import tempfile

    module = site_root / "js" / "site.js"
    if not module.is_file():
        print("FAILED: the reverse test needs the shared module, and there is no js/site.js")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # ── leg one: a name is called and never defined ──
        (root / "one.html").write_text(
            "<html><body><script>\n"
            "function escapeHTML(val) { return val; }\n"
            "function panel() { return escapeHTML('x'); }\n"
            "</script></body></html>\n",
            encoding="utf-8",
        )
        (root / "two.html").write_text(
            "<html><body><script>\n"
            "function panel() { return escapeHTML('x'); }\n"
            "</script></body></html>\n",
            encoding="utf-8",
        )
        findings = scan(root)
        names = {f["name"] for f in findings}
        pages = {Path(str(f["page"])).name for f in findings}
        if "escapeHTML" not in names or "two.html" not in pages or "one.html" in pages:
            print("FAILED: the reverse test did not detect the injected page")
            print(f"  findings: {findings}")
            return 1
        print("OK: the missing-helper leg is detected "
              "(and the page that defines its own copy stays quiet)")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "js").mkdir()
        (root / "js" / "site.js").write_text(
            module.read_text(encoding="utf-8"), encoding="utf-8")
        # A second copy of a shared helper.
        (root / "three.html").write_text(
            "<html><body><script>\n"
            "function escapeHTML(val) { return val; }\n"
            "</script></body></html>\n",
            encoding="utf-8",
        )
        # The page that loads the module: quiet.
        (root / "four.html").write_text(
            '<html><body><script src="/js/site.js"></script>\n'
            "<script>\n"
            "function panel() { return escapeHTML('x'); }\n"
            "</script></body></html>\n",
            encoding="utf-8",
        )
        # A local shadow is a local business, not a second copy: quiet.
        (root / "five.html").write_text(
            "<html><body><script>\n"
            "function panel() { const escapeHTML = (v) => v; return escapeHTML('x'); }\n"
            "</script></body></html>\n",
            encoding="utf-8",
        )
        findings = scan(root)
        kinds = {f["kind"] for f in findings}
        pages = {Path(str(f["page"])).name for f in findings}
        if kinds != {"duplicate"} or "three.html" not in pages \
                or "four.html" in pages or "five.html" in pages:
            print("FAILED: the reverse test did not detect the second copy")
            print(f"  findings: {findings}")
            return 1
        print("OK: the second-copy leg is detected "
              "(and the pages that load the module, or shadow the name locally, stay quiet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

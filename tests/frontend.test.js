// The frontend shield: safeFetchLessons / buildErrorHTML / isValidLesson.
//
// And, since 2026-10-26, the site's shared module `docs/js/site.js`: escapeHTML /
// readJsonResponse.
//
// Why `node:test` and not vitest: this file used to `import { describe, it, expect,
// vi } from 'vitest'`, which the root `package.json` does not depend on and
// `node_modules/vitest` does not contain, so it had never run — and because
// `pytest` only collects `test_*.py`, it was invisible to the `audit` job too. The
// same shape as `tests/dsh/`, which #2920 deleted. Rather than add a dependency
// and a lockfile regeneration for one file, this uses the runner the other 74
// worker tests and `packages/fatal-guard/tests/smoke.test.js` already use.
//
// The shared module's tests live here rather than in a file of their own: a second
// root-level `tests/*.test.js` would be executed by nothing — `fatal-guard.yml`'s
// `node --test 'tests/*.test.js'` runs with `working-directory: packages/fatal-guard`,
// so it reaches `packages/fatal-guard/tests/*.test.js` and not this tree's — and
// `tests/test_workflow_inventory.py` exists to say exactly that.
//
// Run: node --test tests/frontend.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

import {
  safeFetchLessons,
  buildErrorHTML,
  isValidLesson,
} from '../docs/js/core.js';

// Minimal DOM shim: the production code assigns through `errorUI`, and the test
// needs somewhere for that to land.
function setupDOM() {
  let innerHTML = '';
  const el = {
    get innerHTML() { return innerHTML; },
    set innerHTML(v) { innerHTML = String(v); },
    style: { display: '' },
    getElementById: () => el,
  };
  globalThis.document = { getElementById: () => el };
  return el;
}

// `safeFetchLessons` logs to console.error on every blocked load, which is the
// behaviour under test — so silence it for the duration rather than leaving the
// output unreadable, and put it back afterwards.
function silenceConsoleError() {
  const original = console.error;
  console.error = () => {};
  return () => { console.error = original; };
}

// A `fetch` stub returning one canned response, then restoring the real one.
function stubFetch(impl) {
  const original = globalThis.fetch;
  globalThis.fetch = impl;
  return () => { globalThis.fetch = original; };
}

test('🚨 lessons.json root is not an array → error boundary, empty result', async () => {
  const searchEl = setupDOM();
  const restoreLog = silenceConsoleError();
  const restoreFetch = stubFetch(async () => ({
    ok: true,
    json: async () => ({ last_updated: 12345, lessons: 'not-an-array' }),
  }));
  try {
    const lessons = await safeFetchLessons('./lessons.json', (msg) => {
      searchEl.innerHTML = buildErrorHTML(msg);
    });
    assert.deepEqual(lessons, []);
    assert.ok(searchEl.innerHTML.includes('Frontend Shield'));
    assert.ok(searchEl.innerHTML.includes('lessons.json'));
  } finally {
    restoreFetch();
    restoreLog();
  }
});

test('🧹 valid JSON with invalid entries → the invalid ones are filtered out', async () => {
  const restoreFetch = stubFetch(async () => ({
    ok: true,
    json: async () => [
      { id: 'v1', title: 'Good', domain: 'python' },
      { id: 'b1', title: null, domain: 't' },
      { id: 'b2', domain: 'missing-title' },
      null,
      { id: 'v2', title: 'Another', domain: 'devops' },
    ],
  }));
  try {
    const lessons = await safeFetchLessons('./lessons.json');
    assert.equal(lessons.length, 2);
    assert.equal(lessons[0].title, 'Good');
    assert.equal(lessons[1].title, 'Another');
  } finally {
    restoreFetch();
  }
});

test('🧹 isValidLesson accepts what it should and rejects what it should not', () => {
  assert.equal(isValidLesson({ title: 'a', domain: 'b' }), true);
  // An empty title still type-checks; the gate is `typeof`, not emptiness.
  assert.equal(isValidLesson({ title: '', domain: 'b' }), true);
  assert.equal(isValidLesson({ title: 'a' }), false);
  assert.ok(!isValidLesson(null));
  assert.equal(isValidLesson({}), false);
});

test('🚨 network failure → error boundary, empty result', async () => {
  const searchEl = setupDOM();
  const restoreLog = silenceConsoleError();
  const restoreFetch = stubFetch(async () => { throw new Error('Network failure'); });
  try {
    const lessons = await safeFetchLessons('./lessons.json', (msg) => {
      searchEl.innerHTML = buildErrorHTML(msg);
    });
    assert.deepEqual(lessons, []);
    assert.ok(searchEl.innerHTML.includes('Frontend Shield'));
  } finally {
    restoreFetch();
    restoreLog();
  }
});

test('🧹 buildErrorHTML escapes the message it is handed', () => {
  const html = buildErrorHTML('test <script> malicious');
  assert.ok(html.includes('test'));
  assert.ok(!html.includes('<script>'));
  // The message is URI-encoded, so the tag arrives as text rather than markup.
  assert.ok(html.includes(encodeURIComponent('<script>')));
});

// Dropped from the vitest version: "XSS script 注入被 DOMPurify 拦截" asserted on a
// `DOMPurify` **mock this file defined itself**, so it exercised no production code and
// passed no matter what `core.js` did — a green that meant nothing. The test above
// covers the same property against `buildErrorHTML`, which is the code that actually
// builds HTML. `core.js` does not use DOMPurify at all.

// ── the shared site module (docs/js/site.js) ───────────────────────────────────────────────────
//
// The escaper and the JSON reader used to be copied into seven pages, and the copies drifted
// apart: four escaped with `String(value)` (so a null rendered as the words "null"), one with
// `String(value || '')` (so a count of zero rendered as nothing at all), and two with
// `String(val ?? '')`. One copy lives in `docs/js/site.js` now, and the tests below pin the
// behaviour the copies disagreed about — a canonical copy that nobody pins drifts again.
//
// `docs/js/site.js` is a **classic script**, not a module: a module's scope is not visible to
// the pages' classic inline scripts, and a module script is deferred, so it would run *after*
// them and the pages would not see it. It is therefore loaded here the way the browser loads
// it — evaluated as a script, with the names it declares landing on the global object.

const SITE_MODULE_SOURCE = readFileSync(new URL('../docs/js/site.js', import.meta.url), 'utf8');

// The module-syntax check below looks at *code*, not at prose: a comment whose line begins with
// the word "export" is not a module, and a gate that says otherwise is a gate that cries wolf.
// A dynamic `import()` is legal in a classic script, so only a *static* import counts.
const STATIC_MODULE_SYNTAX = /^\s*(?:export\b|import\s+[A-Za-z_*'"{])/m;

const SITE_MODULE_CODE = SITE_MODULE_SOURCE
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

function loadSiteModule() {
  const context = vm.createContext({});
  vm.runInContext(SITE_MODULE_SOURCE, context, { filename: 'docs/js/site.js' });
  return context;
}

const site = loadSiteModule();

// A `Response` stand-in: the reader asks for the body and the content type, and nothing else.
function fakeResponse({ status = 200, contentType = 'application/json', body = '' } = {}) {
  return {
    status,
    headers: {
      get: (name) => (String(name).toLowerCase() === 'content-type' ? contentType : null),
    },
    text: async () => body,
  };
}

test('🧹 the shared module is a classic script, and it publishes both helpers', () => {
  // A module would be invisible to the pages' inline scripts, and deferred behind them.
  assert.ok(!STATIC_MODULE_SYNTAX.test(SITE_MODULE_CODE),
    'docs/js/site.js must stay a classic script: a module scope is not a classic script scope');
  assert.equal(typeof site.escapeHTML, 'function');
  assert.equal(typeof site.readJsonResponse, 'function');
});

test('🧹 escapeHTML turns a null into an empty string, not the word "null"', () => {
  // This is the difference between the canonical `??` and the four copies that used
  // `String(value)`: a null rendered into the page as the words "null".
  assert.equal(site.escapeHTML(null), '');
  assert.equal(site.escapeHTML(undefined), '');
  assert.equal(site.escapeHTML(''), '');
});

test('🧹 escapeHTML keeps a zero and a false — `||` would have thrown them away', () => {
  // The difference between the canonical `??` and the copy that used `||`: a count of zero
  // rendering as nothing is its own small lie.
  assert.equal(site.escapeHTML(0), '0');
  assert.equal(site.escapeHTML(false), 'false');
  assert.equal(site.escapeHTML(NaN), 'NaN');
});

test('🧹 escapeHTML escapes the five characters that make markup', () => {
  assert.equal(site.escapeHTML('<b>x</b>'), '&lt;b&gt;x&lt;/b&gt;');
  assert.equal(site.escapeHTML('a & b'), 'a &amp; b');
  assert.equal(site.escapeHTML('"'), '&quot;');
  assert.equal(site.escapeHTML("'"), '&#39;');
});

test('🧹 readJsonResponse returns the parsed body', async () => {
  const body = { last_updated: 12345, lessons: [{ id: 'x' }] };
  const parsed = await site.readJsonResponse(fakeResponse({
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  }));
  // `JSON.stringify`, not `deepEqual`: the parsed object was built in the module's realm, so
  // its prototype is not this realm's `Object.prototype` and a strict deep-equal would fail on
  // the prototype rather than on the data.
  assert.equal(JSON.stringify(parsed), JSON.stringify(body));
});

test('🚨 readJsonResponse refuses to call an HTML error page JSON', async () => {
  // The failure this reader exists for: a proxy's HTML error page read as "no data", and the
  // panel telling the reader the site was out of date while the API was answering.
  await assert.rejects(
    () => site.readJsonResponse(fakeResponse({
      status: 502,
      contentType: 'text/html',
      body: '<html><body>Bad Gateway</body></html>',
    })),
    /Proxy returned HTML \(502\)/,
  );
});

test('🚨 readJsonResponse refuses a body that is not JSON at all', async () => {
  await assert.rejects(
    () => site.readJsonResponse(fakeResponse({
      status: 500,
      contentType: 'text/plain',
      body: 'upstream connect error',
    })),
    /Non-JSON response \(500\)/,
  );
});

test('🚨 readJsonResponse refuses a body that claims to be JSON and is not', async () => {
  await assert.rejects(
    () => site.readJsonResponse(fakeResponse({
      contentType: 'application/json',
      body: '{not json',
    })),
    /Invalid JSON response \(200\)/,
  );
});
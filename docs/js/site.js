/**
 * The site's shared helpers — one copy of the rules that used to be copied per page.
 *
 * **Why this file exists.** `escapeHTML` lived in seven pages and `readJsonResponse` in two, and
 * the copies had already drifted apart: four pages escaped with `String(value)` (so a null
 * rendered as the words "null"), one with `String(value || '')` (so a count of zero rendered as
 * nothing at all), and two with `String(val ?? '')`. Copy-paste was the root cause of the
 * incident this repository has already paid for: on 2026-10-22 the homepage's panels went blank
 * while `/api/activity` was answering 200, because #3694 moved the registration form to
 * `/register/` and took the two helpers with it — the form was the only thing there that used
 * them as a form, and the panels kept calling them. A gate can find that shape; only one copy
 * prevents it.
 *
 * **It is a classic script, not a module.** The pages' own scripts are classic scripts, and a
 * module's scope is not a classic script's scope: the helpers would not be visible to the code
 * that calls them. A module script is also deferred, so it would run *after* the pages' inline
 * scripts. A plain `<script src="/js/site.js">` placed before them runs in order and puts the
 * helpers where the pages expect them.
 *
 * **The canonical escape is `String(val ?? '')`.** A null or an undefined becomes an empty
 * string — the alternative, `String(val)`, prints the words "null" and "undefined" into the
 * page — while `0` and `false` keep their meaning. `||` would turn both into an empty string,
 * and a count of zero rendering as nothing is its own small lie.
 */
function escapeHTML(val) {
  return String(val ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

/**
 * The JSON reader every fetch on the site goes through.
 *
 * It was the second half of #3694: the homepage's `fetchWithTimeout` and `fetchPOST` both end in
 * `readJsonResponse(r)`, so every JSON the page fetched threw a `ReferenceError`, the panel's own
 * `catch` swallowed it, and the reader was told the site's data was out of date. The body below
 * is the one the homepage and /register/ have been running since 2026-10-22, moved here rather
 * than rewritten: a reader that turns a proxy's HTML error page into a sentence about the site's
 * age is the one thing here worth keeping.
 */
async function readJsonResponse(response) {
  const text = await response.text();
  const contentType = response.headers.get('content-type') || '';
  const snippet = text.slice(0, 80);
  if (!contentType.includes('application/json')) {
    if (snippet.trimStart().startsWith('<')) {
      throw new Error(`Proxy returned HTML (${response.status}). Refresh and try again, or use GitHub Issue registration.`);
    }
    throw new Error(`Non-JSON response (${response.status}): ${snippet}`);
  }
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new Error(`Invalid JSON response (${response.status}): ${snippet}`);
  }
}

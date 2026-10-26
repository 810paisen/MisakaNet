"""Static contract tests for the contributor reputation leaderboard (#908)."""

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "docs" / "insights" / "reputation-leaderboard.html"
WORKER = REPO_ROOT / "workers" / "register-proxy-sw.js"
UTILS = REPO_ROOT / "workers" / "lib" / "utils.js"


class TestReputationLeaderboardPage(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        self.worker = WORKER.read_text(encoding="utf-8")
        self.utils = UTILS.read_text(encoding="utf-8")

    def test_page_and_endpoint_are_wired(self):
        self.assertTrue(PAGE.exists())
        self.assertIn("/api/insights/reputation-leaderboard", self.html)
        self.assertIn('value="all-time"', self.html)
        self.assertIn('value="monthly"', self.html)
        self.assertIn('value="weekly"', self.html)
        self.assertIn('url.pathname === "/api/insights/reputation-leaderboard"', self.worker)

    def test_page_is_responsive_and_uses_the_shared_escaper(self):
        self.assertIn("@media (max-width: 520px)", self.html)
        self.assertNotIn("cdn.", self.html)
        # The escaper is not copied into the page any more. One copy lives in `docs/js/site.js`
        # and every page loads it: the seven copies are how they drifted apart in the first
        # place, and #3694 is what that cost. A second copy here would be the shape again.
        self.assertIn('<script src="/js/site.js"></script>', self.html)
        self.assertNotIn("function escapeHTML", self.html)
        # ...and the page still escapes what it renders: the helper moved, the duty did not.
        self.assertIn("escapeHTML(", self.html)

    def test_page_discloses_non_monetary_points(self):
        self.assertIn("no cash value", self.html.lower())
        self.assertIn('cashValue: false', self.worker)
        self.assertIn('pointsSource: "data/contributor-points.json"', self.worker)

    def test_backend_caps_and_filters_the_public_shape(self):
        self.assertIn("REPUTATION_MAX_ENTRIES = 20", self.worker)
        self.assertIn('"all-time": { value: null', self.utils)
        self.assertIn("historyPoints", self.worker)
        self.assertIn("totalPoints", self.worker)


if __name__ == "__main__":
    unittest.main()

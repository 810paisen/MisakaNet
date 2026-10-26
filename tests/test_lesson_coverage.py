"""Static contract tests for the lesson coverage dashboard (#905)."""

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "docs" / "insights" / "lesson-coverage.html"
WORKER = REPO_ROOT / "workers" / "register-proxy-sw.js"


class TestLessonCoverageDashboard(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        self.worker = WORKER.read_text(encoding="utf-8")

    def test_endpoint_and_page_are_wired(self):
        self.assertTrue(PAGE.exists())
        self.assertIn("/api/insights/lesson-coverage", self.html)
        self.assertIn('url.pathname === "/api/insights/lesson-coverage"', self.worker)
        self.assertIn("buildLessonCoverage", self.worker)

    def test_dashboard_has_metrics_chart_and_gaps_table(self):
        for marker in ("publishedLessons", "coveragePercent", "gapCount", "unsolvedFamilyCount", "id=\"chart\"", "gaps-table"):
            self.assertIn(marker, self.html)
        self.assertIn("needs-review", self.html)
        self.assertIn("uncovered", self.html)

    def test_page_is_responsive_and_escaped_by_the_shared_module(self):
        self.assertIn("@media (max-width: 700px)", self.html)
        self.assertIn("@media (max-width: 460px)", self.html)
        self.assertNotIn("cdn.", self.html)
        # The escaper is not copied into the page any more. One copy lives in `docs/js/site.js`
        # and every page loads it: the seven copies are how they drifted apart in the first
        # place, and #3694 is what that cost. A second copy here would be the shape again.
        self.assertIn('<script src="/js/site.js"></script>', self.html)
        self.assertNotIn("function escapeHTML", self.html)
        # ...and the page still escapes what it renders: the helper moved, the duty did not.
        self.assertIn("escapeHTML(", self.html)

    def test_privacy_contract_is_explicit(self):
        self.assertIn("Raw search queries", self.html)
        self.assertIn('raw_query: false', self.worker)
        self.assertIn('pii: false', self.worker)


if __name__ == "__main__":
    unittest.main()

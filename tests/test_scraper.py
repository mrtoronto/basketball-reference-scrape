import unittest
from urllib.error import URLError

import scraper


class ScraperIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.season = scraper.detect_latest_season()
        except URLError as exc:
            self.skipTest(f"network unavailable: {exc}")

    def test_detect_latest_season_returns_recent_year(self):
        self.assertGreaterEqual(self.season, 2020)

    def test_scrape_player_per_game_returns_rows(self):
        rows = scraper.scrape_player_per_game(self.season)
        self.assertTrue(rows, "expected at least one player row")
        self.assertIn("Player", rows[0])

    def test_scrape_player_game_logs_last_15(self):
        rows = scraper.scrape_player_game_logs("jamesle01", self.season, 15)
        self.assertTrue(0 < len(rows) <= 15)
        self.assertIn("Date", rows[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

# Basketball Reference Scraper

Simple command line helper for scraping player lists and individual box score logs from [Basketball Reference](https://www.basketball-reference.com/).

## Requirements

* Python 3.9+
* Standard library only (no third-party packages required)

The script relies on `urllib` from the Python standard library to download HTML and a tiny `HTMLParser` helper to read table data. Every request sends a desktop browser user-agent string to stay polite to the website.

## Usage

Run the script with `python scraper.py <command> [options]`.

### 1. Download per-game stats for a season

```
python scraper.py players --season 2024 --output players_2024.csv
```

* `--season` is the season year used by Basketball Reference (e.g. `2024` corresponds to the 2023‑24 season). If omitted, the script automatically discovers the most recent season available on the site.
* `--output` is optional. When skipped, the file is written as `players_<season>.csv`.

The CSV contains every column shown on the per-game stats page, one row per player/team combination.

### 2. Download the last N game logs for one or more players

```
python scraper.py game-logs jamesle01 doncilo01 --season 2024 --last 15 --output-dir game_logs
```

* Provide one or more Basketball Reference player IDs (the part of their player URL, e.g. `jamesle01`).
* `--season` and `--last` behave like above (defaults: latest season and 15 games).
* A separate CSV is written for each player to the chosen output directory (defaults to `./game_logs`).

Each CSV mirrors the basic game log table, trimmed to the last N games of that season for the player.

## Testing the scraper

Simple integration tests prove that Basketball Reference can be scraped. Run them with:

```
python -m unittest discover
```

The tests download live data and will skip automatically if the network is unavailable or the site cannot be reached. They will fail if the site layout changes.

## Notes

* Respect Basketball Reference's terms of use and avoid making excessive requests.
* The script only fetches the “Basic” game log table (`pgl_basic`). If you need advanced stats you can extend `scrape_player_game_logs` to read a different table id.

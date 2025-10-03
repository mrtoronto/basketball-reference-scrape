# Basketball Reference Scraper

Command‑line helper to pull player lists and game logs from Basketball Reference.

## Get started

1) MacOS Setup

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2) Install dependencies

```
uv sync
```

3) Pull some data (season year is when the season ends; 2024 = 2023–24)

```
# Look up a player's ID by name
uv run br-scraper lookup "LeBron James"

# Last 15 games for a couple players → CSVs in ./game_logs
uv run br-scraper game-logs jamesle01 doncilu01 --season 2024 --last 15 --output-dir game_logs

# All games for many players → one combined CSV
uv run br-scraper game-logs --input-file ids.txt --season 2024 --all-games --combined-output all_logs_2024.csv

# Per‑game stats for a season
uv run br-scraper players --season 2024 --output players_2024.csv
```

Tip: A player ID is the end of their profile URL. Example `https://www.basketball-reference.com/players/j/jamesle01.html` → `jamesle01`. Make an `ids.txt` with one ID per line; `#` starts a comment.

## Use from Python (optional)

```
import scraper
rows = scraper.scrape_player_game_logs("jamesle01", 2024, last_n=15)
```

The notebook `notebooks/demo.ipynb` shows end‑to‑end examples.

## Tests

```
uv run python -m unittest discover
```

## Notes

- Be polite. Keep request volume reasonable and follow the site's terms.
- This reads the basic game log table. Extend `scrape_player_game_logs` if you need different tables.

"""Command-line scraper for Basketball Reference player data and game logs."""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://www.basketball-reference.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


class ScrapeError(RuntimeError):
    """Raised when the Basketball Reference page cannot be parsed."""


@dataclass
class Table:
    headers: Sequence[str]
    rows: List[Sequence[str]]

    def as_dicts(self) -> List[dict]:
        return [dict(zip(self.headers, row)) for row in self.rows]


class TableParser(HTMLParser):
    """Extracts a table by id from HTML."""

    def __init__(self, table_id: str) -> None:
        super().__init__()
        self.target_id = table_id
        self.in_table = False
        self.in_thead = False
        self.in_tbody = False
        self.current_row: List[str] = []
        self.headers: List[str] = []
        self.rows: List[List[str]] = []
        self.capture_cell = False
        self.current_cell: List[str] = []
        self.skip_row = False

    def handle_starttag(self, tag: str, attrs):
        attrs_dict = {key: value for key, value in attrs}
        if tag == "table" and attrs_dict.get("id") == self.target_id:
            self.in_table = True
            return
        if not self.in_table:
            return
        if tag == "thead":
            self.in_thead = True
        elif tag == "tbody":
            self.in_tbody = True
        elif tag == "tr":
            classes = attrs_dict.get("class", "")
            self.skip_row = "thead" in classes.split()
            self.current_row = []
        elif tag in {"th", "td"}:
            if self.skip_row:
                return
            self.capture_cell = True
            self.current_cell = []
        elif tag == "br" and self.capture_cell:
            self.current_cell.append(" ")

    def handle_endtag(self, tag: str):
        if not self.in_table:
            return
        if tag == "table":
            self.in_table = False
        elif tag == "thead":
            self.in_thead = False
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "tr":
            if not self.skip_row and self.current_row:
                if self.in_thead:
                    self.headers = self.current_row
                elif self.in_tbody:
                    self.rows.append(self.current_row)
            self.current_row = []
            self.skip_row = False
        elif tag in {"th", "td"}:
            if self.capture_cell:
                text = "".join(self.current_cell).strip()
                self.current_row.append(text)
            self.capture_cell = False
            self.current_cell = []

    def handle_data(self, data: str):
        if self.capture_cell:
            self.current_cell.append(data)


def fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        text = response.read().decode(charset, errors="ignore")
    return text.replace("<!--", "").replace("-->", "")


def parse_table(html: str, table_id: str) -> Table:
    parser = TableParser(table_id)
    parser.feed(html)
    if not parser.headers:
        raise ScrapeError(f"Table '{table_id}' was not found on the page.")
    cleaned_rows = [
        row for row in parser.rows if row and len(row) == len(parser.headers)
    ]
    if not cleaned_rows:
        raise ScrapeError(f"Table '{table_id}' did not contain any rows.")
    return Table(headers=parser.headers, rows=cleaned_rows)


def detect_latest_season() -> int:
    html = fetch_html(f"{BASE_URL}/leagues/")
    matches = re.findall(r"/leagues/NBA_(\d{4})\.html", html)
    if matches:
        return max(int(year) for year in matches)
    table = parse_table(html, "leagues_active")
    for row in table.rows:
        season_label = row[0]
        if "-" in season_label:
            left, right = season_label.split("-", 1)
            try:
                start_year = int(left)
            except ValueError:
                continue
            try:
                end_suffix = int(right)
            except ValueError:
                continue
            end_year = start_year // 100 * 100 + end_suffix
            if end_year < start_year:
                end_year += 100
            return end_year
        try:
            return int(season_label)
        except ValueError:
            continue
    raise ScrapeError("Could not detect the latest NBA season.")


def scrape_player_per_game(season: int) -> List[dict]:
    url = f"{BASE_URL}/leagues/NBA_{season}_per_game.html"
    html = fetch_html(url)
    table = parse_table(html, "per_game_stats")
    return table.as_dicts()


def scrape_player_game_logs(player_id: str, season: int, last_n: int) -> List[dict]:
    first_letter = player_id[0]
    url = f"{BASE_URL}/players/{first_letter}/{player_id}/gamelog/{season}"
    html = fetch_html(url)
    table = parse_table(html, "pgl_basic")
    rows = table.rows[-last_n:] if last_n else table.rows
    return [dict(zip(table.headers, row)) for row in rows]


def write_csv(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        raise ScrapeError("No rows to write.")
    headers = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    players_parser = subparsers.add_parser(
        "players", help="Download per-game stats for all players in a season"
    )
    players_parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="NBA season year (e.g. 2024 for the 2023-24 season). Defaults to the latest season.",
    )
    players_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV file. Defaults to players_<season>.csv",
    )

    logs_parser = subparsers.add_parser(
        "game-logs", help="Download the last N game logs for one or more players"
    )
    logs_parser.add_argument(
        "player_ids",
        nargs="+",
        help="Basketball Reference player identifiers (e.g. jamesle01).",
    )
    logs_parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="NBA season year. Defaults to the latest season.",
    )
    logs_parser.add_argument(
        "--last",
        type=int,
        default=15,
        help="How many recent games to keep from the season (default: 15).",
    )
    logs_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("game_logs"),
        help="Directory where CSV files will be written (default: game_logs).",
    )

    return parser.parse_args(argv)


def command_players(season: Optional[int], output: Optional[Path]) -> None:
    target_season = season or detect_latest_season()
    rows = scrape_player_per_game(target_season)
    output_path = output or Path(f"players_{target_season}.csv")
    write_csv(output_path, rows)
    print(f"Saved per-game stats for season {target_season} to {output_path}")


def command_game_logs(
    player_ids: Sequence[str], season: Optional[int], last: int, output_dir: Path
) -> None:
    target_season = season or detect_latest_season()
    for player_id in player_ids:
        rows = scrape_player_game_logs(player_id, target_season, last)
        output_path = output_dir / f"{player_id}_last{last}_{target_season}.csv"
        write_csv(output_path, rows)
        print(
            f"Saved last {min(last, len(rows))} games for {player_id} "
            f"(season {target_season}) to {output_path}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "players":
            command_players(args.season, args.output)
        elif args.command == "game-logs":
            command_game_logs(args.player_ids, args.season, args.last, args.output_dir)
        else:
            raise ScrapeError(f"Unknown command: {args.command}")
    except (HTTPError, URLError) as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1
    except ScrapeError as exc:
        print(f"Failed to scrape data: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

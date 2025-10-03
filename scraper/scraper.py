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
            # Only skip header-repeat rows that appear inside <tbody>.
            # Some pages mark actual header rows inside <thead> with class "thead" as well.
            self.skip_row = self.in_tbody and "thead" in classes.split()
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
    # Replace HTML comment markers with spaces so that attribute boundaries
    # do not get accidentally concatenated (Basketball-Reference often wraps
    # table HTML in comments).
    return text.replace("<!--", " ").replace("-->", " ")


def search_player_ids(query: str) -> List[dict]:
    """Search Basketball-Reference for players by name and return candidate IDs.

    Returns a list of dicts with fields: id, name, url.
    """
    from urllib.parse import quote_plus

    url = f"{BASE_URL}/search/search.fcgi?search={quote_plus(query)}"
    html = fetch_html(url)
    # Extract search-item blocks (non-greedy across newlines)
    blocks = re.findall(r'(<div class=\"search-item\">[\s\S]*?</div>)', html, flags=re.S)
    results: List[dict] = []
    for block in blocks:
        # Name (may include years)
        m_name = re.search(r'<div class=\"search-item-name\">[\s\S]*?<a [^>]*>([^<]+)</a>', block)
        display_name = (m_name.group(1).strip() if m_name else None) or ""
        # URL
        m_url = re.search(r'<div class=\"search-item-url\">\s*([^<]+)\s*<', block)
        url_path = m_url.group(1).strip() if m_url else None
        if not url_path:
            # Fallback: read from the anchor tag
            m_href = re.search(r'<a[^>]+href=\"([^\"]+)\"', block)
            url_path = m_href.group(1).strip() if m_href else None
        if not url_path:
            continue
        # Only accept player profile links; IDs are typically 8-10 chars
        m_id = re.match(r"/players/[a-z]/([a-z0-9]{7,12})\.html$", url_path)
        if not m_id:
            continue
        player_id = m_id.group(1)
        results.append({"id": player_id, "name": display_name, "url": f"{BASE_URL}{url_path}"})
    return results


def parse_table(html: str, table_id: str) -> Table:
    parser = TableParser(table_id)
    parser.feed(html)
    if not parser.headers:
        # Fallback: extract only the table's HTML by id and parse that slice.
        id_token = f'id="{table_id}"'
        idx = html.find(id_token)
        if idx != -1:
            start = html.rfind("<table", 0, idx)
            end = html.find("</table>", idx)
            if start != -1 and end != -1:
                snippet = html[start : end + len("</table>")]
                parser = TableParser(table_id)
                parser.feed(snippet)
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
    candidates = [int(y) for y in re.findall(r"/leagues/NBA_(\d{4})\.html", html)]
    # If regex links are not present, fall back to parsing the active leagues table
    if not candidates:
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
                candidates.append(end_year)
                break
            else:
                try:
                    candidates.append(int(season_label))
                    break
                except ValueError:
                    continue
    if not candidates:
        raise ScrapeError("Could not detect the latest NBA season.")

    # Validate that the per-game stats table exists for the chosen season.
    # Some very recent seasons may not yet have populated pages/tables.
    tried: set[int] = set()
    for year in sorted(set(candidates), reverse=True):
        tried.add(year)
        try:
            per_url = f"{BASE_URL}/leagues/NBA_{year}_per_game.html"
            per_html = fetch_html(per_url)
            _ = parse_table(per_html, "per_game_stats")
            return year
        except ScrapeError:
            continue

    # As a final fallback, walk back up to 5 additional years from the newest candidate
    newest = max(candidates)
    for delta in range(1, 6):
        year = newest - delta
        if year in tried:
            continue
        try:
            per_url = f"{BASE_URL}/leagues/NBA_{year}_per_game.html"
            per_html = fetch_html(per_url)
            _ = parse_table(per_html, "per_game_stats")
            return year
        except ScrapeError:
            continue

    raise ScrapeError("Could not find a recent NBA season with per-game stats available.")


def scrape_player_per_game(season: int) -> List[dict]:
    url = f"{BASE_URL}/leagues/NBA_{season}_per_game.html"
    html = fetch_html(url)
    table = parse_table(html, "per_game_stats")
    return table.as_dicts()


def scrape_player_game_logs(player_id: str, season: int, last_n: int) -> List[dict]:
    first_letter = player_id[0]
    # Try the requested season first, then walk back up to 4 prior seasons
    # in case the latest season's game logs are not yet published.
    seasons_to_try: List[int] = [season]
    for delta in range(1, 5):
        if season - delta > 2000:  # avoid going too far back unnecessarily
            seasons_to_try.append(season - delta)

    last_error: Optional[Exception] = None
    for target_year in seasons_to_try:
        try:
            url = f"{BASE_URL}/players/{first_letter}/{player_id}/gamelog/{target_year}"
            html = fetch_html(url)
            # Newer pages may use different table ids; try known possibilities.
            table_ids_to_try: Sequence[str] = [
                "pgl_basic",               # historical id
                "player_game_log_reg",     # current regular season id
            ]
            table: Optional[Table] = None
            for table_id in table_ids_to_try:
                try:
                    table = parse_table(html, table_id)
                    break
                except ScrapeError:
                    table = None
            if table is None:
                raise ScrapeError("No known game log table id found")
            rows = table.rows[-last_n:] if last_n else table.rows
            return [dict(zip(table.headers, row)) for row in rows]
        except ScrapeError as exc:
            last_error = exc
            continue
    raise ScrapeError(
        f"Game log table was not found for player '{player_id}' in seasons {seasons_to_try}: {last_error}"
    )


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
        "--input-file",
        type=Path,
        default=None,
        help="Optional file with one player id per line to include.",
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
        "--all-games",
        action="store_true",
        help="Fetch all games for the season (overrides --last).",
    )
    logs_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("game_logs"),
        help="Directory where CSV files will be written (default: game_logs).",
    )
    logs_parser.add_argument(
        "--combined-output",
        type=Path,
        default=None,
        help="If provided, write all players' logs to a single CSV at this path.",
    )

    lookup_parser = subparsers.add_parser(
        "lookup", help="Look up Basketball Reference player IDs by name"
    )
    lookup_parser.add_argument(
        "name",
        nargs="+",
        help="Player name to search (e.g. LeBron James).",
    )

    return parser.parse_args(argv)


def command_players(season: Optional[int], output: Optional[Path]) -> None:
    target_season = season or detect_latest_season()
    rows = scrape_player_per_game(target_season)
    output_path = output or Path(f"players_{target_season}.csv")
    write_csv(output_path, rows)
    print(f"Saved per-game stats for season {target_season} to {output_path}")


def _read_player_ids_from_file(path: Path) -> List[str]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except FileNotFoundError:
        raise ScrapeError(f"Input file not found: {path}")


def command_game_logs(
    player_ids: Sequence[str],
    season: Optional[int],
    last: int,
    output_dir: Path,
    all_games: bool = False,
    input_file: Optional[Path] = None,
    combined_output: Optional[Path] = None,
) -> None:
    # Merge player ids from file if provided
    merged_ids: List[str] = list(player_ids)
    if input_file is not None:
        merged_ids.extend(_read_player_ids_from_file(input_file))
    # De-duplicate while preserving order
    seen = set()
    merged_ids = [pid for pid in merged_ids if not (pid in seen or seen.add(pid))]
    if not merged_ids:
        raise ScrapeError("No player ids provided.")

    target_season = season or detect_latest_season()
    effective_last = 0 if all_games else last

    if combined_output is not None:
        combined_rows: List[dict] = []
        for player_id in merged_ids:
            rows = scrape_player_game_logs(player_id, target_season, effective_last)
            # ensure we include the player id for downstream filtering
            for row in rows:
                row_with_id = dict(row)
                row_with_id["PlayerID"] = player_id
                combined_rows.append(row_with_id)
        write_csv(combined_output, combined_rows)
        label = "all" if effective_last == 0 else f"last {effective_last}"
        print(
            f"Saved {label} games for {len(merged_ids)} players (season {target_season}) "
            f"to {combined_output}"
        )
        return

    # Default: write a separate file per player
    for player_id in merged_ids:
        rows = scrape_player_game_logs(player_id, target_season, effective_last)
        label = "all" if effective_last == 0 else f"last{effective_last}"
        output_path = output_dir / f"{player_id}_{label}_{target_season}.csv"
        write_csv(output_path, rows)
        print(
            f"Saved {('all' if effective_last == 0 else f'min({effective_last}, len(rows))')} games for {player_id} "
            f"(season {target_season}) to {output_path}"
        )


def command_lookup(name_parts: Sequence[str]) -> None:
    query = " ".join(name_parts)
    results = search_player_ids(query)
    if not results:
        print(f"No players found for: {query}")
        return
    # Print a concise listing
    for item in results[:20]:
        print(f"{item['id']}: {item['name']} ({item['url']})")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "players":
            command_players(args.season, args.output)
        elif args.command == "game-logs":
            command_game_logs(
                args.player_ids,
                args.season,
                args.last,
                args.output_dir,
                all_games=args.all_games,
                input_file=args.input_file,
                combined_output=args.combined_output,
            )
        elif args.command == "lookup":
            command_lookup(args.name)
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

"""Simple OpenLigaDB Bundesliga data fetcher for quick smoke tests.

This script queries the free OpenLigaDB REST interface to retrieve
Bundesliga fixtures and standings without requiring an authenticated API
key.  It is intentionally lightweight so it can act as a baseline for
further experiments when commercial data providers are unavailable.

Usage examples:
    python scripts/openligadb_fetch.py                 # latest season, next fixtures
    python scripts/openligadb_fetch.py --matchday 10   # fixtures for matchday 10
    python scripts/openligadb_fetch.py --season 2022   # previous season data
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Any, Iterable, List, Mapping, Optional, Tuple

import requests

API_BASE = "https://api.openligadb.de"
DEFAULT_LEAGUE_SHORT = "bl1"  # 1. Bundesliga


def _request_json(path: str) -> Any:
    url = f"{API_BASE}/{path.lstrip('/')}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise SystemExit(f"HTTP request failed for {url!r}: {exc}") from exc
    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - unexpected payload
        raise SystemExit(f"Non-JSON response from {url!r}") from exc


def _extract_year(raw_season: Any) -> Optional[int]:
    if raw_season is None:
        return None
    season_str = str(raw_season)
    # Common formats are "2023" or "2023/2024" – grab the first four digits.
    for token in season_str.replace("/", " ").split():
        try:
            year = int(token)
        except ValueError:
            continue
        if 1900 < year < 2100:
            return year
    return None


def fetch_available_seasons(league_short: str) -> List[int]:
    payload = _request_json("getavailableleagues")
    seasons: List[int] = []
    short_normalized = league_short.lower()
    for item in payload:
        shortcut = str(item.get("leagueShortcut", "")).lower()
        if shortcut != short_normalized:
            continue
        year = _extract_year(item.get("leagueSeason"))
        if year is None:
            continue
        seasons.append(year)
    seasons = sorted(set(seasons), reverse=True)
    return seasons


def fetch_matchday(league_short: str, season: int, matchday: Optional[int]) -> List[Mapping[str, Any]]:
    if matchday is None:
        path = f"getmatchdata/{league_short}/{season}"
    else:
        path = f"getmatchdata/{league_short}/{season}/{matchday}"
    payload = _request_json(path)
    if not isinstance(payload, list):
        raise SystemExit("Unexpected response format for match data")
    return payload


def _match_has_named_teams(match: Mapping[str, Any]) -> bool:
    team1 = match.get("Team1") or {}
    team2 = match.get("Team2") or {}
    return bool(team1.get("TeamName")) and bool(team2.get("TeamName"))


def _find_latest_useful_season(
    league_short: str,
    seasons: Iterable[int],
    matchday: Optional[int],
) -> Tuple[int, List[Mapping[str, Any]], Optional[int], bool]:
    """Return the newest season that already exposes real match data."""

    seasons_list = list(seasons)
    placeholder_source: Optional[int] = None
    for season in seasons_list:
        matches = fetch_matchday(league_short, season, matchday)
        if any(_match_has_named_teams(match) for match in matches):
            return season, matches, placeholder_source, False
        if placeholder_source is None:
            placeholder_source = season
    if not seasons_list:
        raise SystemExit(f"Keine Saisons für Liga {league_short!r} gefunden")
    fallback_season = seasons_list[0]
    matches = fetch_matchday(league_short, fallback_season, matchday)
    return fallback_season, matches, placeholder_source or fallback_season, True


def fetch_table(league_short: str, season: int) -> List[Mapping[str, Any]]:
    payload = _request_json(f"getbltable/{league_short}/{season}")
    if not isinstance(payload, list):
        raise SystemExit("Unexpected response format for league table")
    return payload


def format_match(match: Mapping[str, Any]) -> str:
    home = match.get("Team1", {}).get("TeamName", "?")
    away = match.get("Team2", {}).get("TeamName", "?")
    result = match.get("MatchResults", [])
    kickoff = match.get("MatchDateTime")
    kickoff_dt: Optional[dt.datetime] = None
    if kickoff:
        try:
            kickoff_dt = dt.datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        except ValueError:
            kickoff_dt = None
    kickoff_str = kickoff_dt.strftime("%Y-%m-%d %H:%M") if kickoff_dt else kickoff or "tbd"
    if result:
        latest = sorted(result, key=lambda r: r.get("ResultOrderID", 0))[-1]
        score = f"{latest.get('PointsTeam1', '?')}:{latest.get('PointsTeam2', '?')}"
    else:
        score = "-"
    matchday = match.get("Group", {}).get("GroupOrderID")
    if isinstance(matchday, int):
        matchday_str = f"MD{matchday:>2}"
    else:
        group_name = match.get("Group", {}).get("GroupName")
        matchday_str = group_name or "MD ?"
    return f"{matchday_str} {kickoff_str} {home} {score} {away}"


def format_table_entry(entry: Mapping[str, Any]) -> str:
    team = entry.get("TeamName", "?")
    rank = entry.get("Rank", "?")
    matches = entry.get("Matches", "?")
    points = entry.get("Points", "?")
    goal_diff = entry.get("GoalDiff", "?")
    return f"{rank:>2}. {team:<24} {matches:>2} Spiele  GD {goal_diff:>+3}  Pkt {points:>3}"


def iter_preview(matches: Iterable[Mapping[str, Any]], limit: int) -> Iterable[str]:
    for idx, match in enumerate(matches):
        if idx >= limit:
            break
        yield format_match(match)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Bundesliga data from OpenLigaDB")
    parser.add_argument(
        "--league-short",
        default=DEFAULT_LEAGUE_SHORT,
        help="Kurzbezeichnung der Liga (Standard: bl1 für 1. Bundesliga)",
    )
    parser.add_argument(
        "--season",
        type=int,
        help="Saisonjahr (z.B. 2023). Standard ist die aktuelle Saison laut OpenLigaDB.",
    )
    parser.add_argument(
        "--matchday",
        type=int,
        help="Spieltag. Ohne Angabe werden alle Spiele der Saison geliefert.",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=5,
        help="Anzahl der Spiele, die in der Übersicht angezeigt werden (Standard: 5)",
    )
    parser.add_argument(
        "--show-table",
        action="store_true",
        help="Neben den Spielen auch die aktuelle Tabelle ausgeben",
    )
    parser.add_argument(
        "--table-season",
        type=int,
        help=(
            "Abweichende Saison für den Tabellenabruf (z.B. 2025). "
            "Standard ist die gleiche Saison wie bei den Spielen."
        ),
    )
    return parser.parse_args(argv)



def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    seasons = fetch_available_seasons(args.league_short)
    if not seasons:
        raise SystemExit(f"Keine Saisons für Liga {args.league_short!r} gefunden")

    placeholder_source: Optional[int] = None
    placeholder_only = False
    requested_season = args.season
    if args.season is not None:
        season = args.season
        if season not in seasons:
            print(
                f"Hinweis: Saison {season} ist für Liga {args.league_short} nicht gelistet. "
                f"Verfügbare Saisons: {', '.join(map(str, seasons))}",
                file=sys.stderr,
            )
            season = seasons[0]
        matches = fetch_matchday(args.league_short, season, args.matchday)
    else:
        season, matches, placeholder_source, placeholder_only = _find_latest_useful_season(
            args.league_short, seasons, args.matchday
        )

    print(f"Liga: {args.league_short} | Saison: {season}")

    if placeholder_source is not None and placeholder_source != season:
        print(
            f"Hinweis: Saison {placeholder_source} enthielt noch keine angesetzten Partien. "
            f"Es wird daher auf {season} zurückgegriffen."
        )

    if placeholder_only:
        print(
            "Hinweis: Die aktuellste Saison enthält bislang nur Platzhalterdaten. "
            "Sobald OpenLigaDB Mannschaften und Termine veröffentlicht, erscheinen sie hier."
        )

    if not matches:
        print("Keine Spiele gefunden.")
    else:
        print(f"Zeige bis zu {args.preview} Spiele:")
        for line in iter_preview(matches, args.preview):
            print("  "+line)

    table_season = args.table_season or requested_season or season
    if args.show_table:
        if table_season != season:
            print(
                "\nTabellen-Saison weicht von den Spielinformationen ab "
                f"({table_season} statt {season})."
            )
        print("\nAktuelle Tabelle:")
        for line in map(
            format_table_entry, fetch_table(args.league_short, table_season)
        ):
            print("  "+line)

    return 0


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    raise SystemExit(main())

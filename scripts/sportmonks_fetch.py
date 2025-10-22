"""Basic Sportmonks Football API smoke test for Bundesliga data.

The script fetches Bundesliga league details, the active season metadata and
sample fixtures to verify connectivity. Set the ``SPORTMONKS_API_TOKEN``
environment variable before running it::

    export SPORTMONKS_API_TOKEN=your_token
    python scripts/sportmonks_fetch.py

The script focuses on read-only requests to keep API usage minimal.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, Iterable, Optional

import requests

BASE_URL = "https://api.sportmonks.com/v3/football"
DEFAULT_LEAGUE_NAME = "Bundesliga"
GERMANY_COUNTRY_ID = 11  # Stable identifier for Germany in Sportmonks


def require_token() -> str:
    token = os.getenv("SPORTMONKS_API_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing SPORTMONKS_API_TOKEN environment variable. "
            "Please export your Sportmonks API token."
        )
    return token


def api_get(endpoint: str, *, token: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute a GET request against the Sportmonks API and return the payload."""
    url = f"{BASE_URL}/{endpoint.strip('/')}"
    query: Dict[str, Any] = {"api_token": token}
    if params:
        query.update(params)
    response = requests.get(url, params=query, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected response format from {url!r}: {payload!r}")
    return payload


def _matches_league(
    candidate: Dict[str, Any], *, name: str, country_id: Optional[int]
) -> bool:
    label = candidate.get("name", "")
    if name.lower() not in label.lower():
        return False
    if country_id is None:
        return True
    country = candidate.get("country") or {}
    candidate_country_id = (
        country.get("id") if isinstance(country, dict) else candidate.get("country_id")
    )
    return candidate_country_id == country_id


def _select_league_candidate(
    payload: Dict[str, Any], *, name: str, country_id: Optional[int]
) -> Optional[Dict[str, Any]]:
    data: Iterable[Dict[str, Any]] = payload.get("data", [])
    for item in data:
        if _matches_league(item, name=name, country_id=country_id):
            return item
    return None


def find_league_id(
    *, token: str, name: str = DEFAULT_LEAGUE_NAME, country_id: Optional[int] = GERMANY_COUNTRY_ID
) -> int:
    """Resolve a league id using the dedicated Sportmonks search endpoint."""

    try:
        search_response = api_get(
            f"leagues/search/{name}",
            token=token,
            params={"include": "country"},
        )
    except requests.HTTPError:
        search_response = {}

    candidate = _select_league_candidate(
        search_response, name=name, country_id=country_id
    )
    if candidate and candidate.get("id"):
        return int(candidate["id"])

    # Fallback to country-filtered listing if search yields no usable result.
    if country_id is not None:
        country_response = api_get(
            f"leagues/countries/{country_id}",
            token=token,
            params={"include": "country"},
        )
        candidate = _select_league_candidate(
            country_response, name=name, country_id=country_id
        )
        if candidate and candidate.get("id"):
            return int(candidate["id"])

    # Full list fallback (still useful when filters are misconfigured)
    fallback_response = api_get(
        "leagues",
        token=token,
        params={"per_page": 200, "include": "country"},
    )
    candidate = _select_league_candidate(
        fallback_response, name=name, country_id=country_id
    )
    if candidate and candidate.get("id"):
        return int(candidate["id"])

    raise LookupError(
        f"Could not find league named '{name}' in Sportmonks response via search endpoint"
    )


def fetch_season_details(season_id: int, *, token: str) -> Dict[str, Any]:
    return api_get(
        f"seasons/{season_id}",
        token=token,
        params={"include": "league,stages"},
    )


def fetch_recent_fixtures(season_id: int, *, token: str, limit: int = 5) -> Dict[str, Any]:
    return api_get(
        f"fixtures/seasons/{season_id}",
        token=token,
        params={"per_page": limit, "include": "participants"},
    )


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sportmonks Bundesliga data fetch test")
    parser.add_argument(
        "--league-id",
        type=int,
        default=None,
        help="Optional league id to fetch directly (skips name lookup)",
    )
    parser.add_argument(
        "--league-name",
        default=DEFAULT_LEAGUE_NAME,
        help="League name substring to search for (default: Bundesliga)",
    )
    parser.add_argument(
        "--country-id",
        type=int,
        default=GERMANY_COUNTRY_ID,
        help="Optional country id filter (default: 11 for Germany; use -1 to disable)",
    )
    parser.add_argument(
        "--fixtures",
        type=int,
        default=5,
        help="Number of fixtures to fetch for the current season (default: 5)",
    )
    parser.add_argument(
        "--show-search",
        action="store_true",
        help="Print the raw league search matches before fetching details",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    try:
        token = require_token()
        country_id = None if args.country_id < 0 else args.country_id

        if args.league_id is not None:
            league_id = args.league_id
        else:
            league_id = find_league_id(
                token=token, name=args.league_name, country_id=country_id
            )

        if args.show_search:
            search_response = api_get(
                f"leagues/search/{args.league_name}",
                token=token,
                params={"include": "country"},
            )
            matches = search_response.get("data", [])
            print("Search matches:")
            for match in matches:
                country = match.get("country") or {}
                country_name = (
                    country.get("name")
                    if isinstance(country, dict)
                    else match.get("country_name")
                )
                print(
                    f"  - {match.get('name')} (ID={match.get('id')}, Country={country_name})"
                )

        league_response = api_get(
            f"leagues/{league_id}",
            token=token,
            params={"include": "country"},
        )
        league = league_response.get("data") or {}
        if not league:
            raise LookupError(f"Could not find league with id {league_id}")

        season_id = league.get("currentseason_id")
        print(f"League: {league['name']} (ID={league_id}, Current Season={season_id})")

        if not season_id:
            print("League has no current season id in the payload; skipping season details", file=sys.stderr)
            return 0

        season = fetch_season_details(season_id, token=token)
        season_data = season.get("data", {})
        print(
            "Season:",
            season_data.get("name"),
            "| Start:",
            season_data.get("start_date"),
            "| End:",
            season_data.get("end_date"),
        )

        fixtures = fetch_recent_fixtures(season_id, token=token, limit=args.fixtures)
        for fixture in fixtures.get("data", []):
            participants = fixture.get("participants", [])
            teams = [team.get("name") for team in participants if team.get("name")]
            print(
                f"Fixture {fixture.get('id')}:",
                fixture.get("starting_at"),
                "| Teams:",
                " vs. ".join(teams) if teams else "-",
            )
    except (RuntimeError, LookupError, requests.HTTPError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

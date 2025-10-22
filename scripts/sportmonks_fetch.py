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


def find_league(
    *,
    token: str,
    name: str = DEFAULT_LEAGUE_NAME,
    country_id: Optional[int] = GERMANY_COUNTRY_ID,
) -> Dict[str, Any]:
    """Return the first league matching by name (and optional country).

    We try multiple lookup strategies to make the helper resilient against
    pagination quirks or naming variations in the API responses.
    """

    def _matches(candidate: Dict[str, Any]) -> bool:
        label = candidate.get("name", "")
        if name.lower() not in label.lower():
            return False
        if country_id is None:
            return True
        country = candidate.get("country") or {}
        candidate_country_id = (
            country.get("id")
            if isinstance(country, dict)
            else candidate.get("country_id")
        )
        return candidate_country_id == country_id

    def _try_candidates(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data: Iterable[Dict[str, Any]] = payload.get("data", [])
        for item in data:
            if _matches(item):
                return item
        return None

    # 1) Use dedicated search endpoint (supports substring matching)
    try:
        search_response = api_get(
            f"leagues/search/{name}",
            token=token,
            params={"include": "country"},
        )
    except requests.HTTPError:
        search_response = {}
    else:
        found = _try_candidates(search_response)
        if found:
            return found

    # 2) Fetch leagues limited to the given country (if provided)
    if country_id is not None:
        country_response = api_get(
            f"leagues/countries/{country_id}",
            token=token,
            params={"include": "country"},
        )
        found = _try_candidates(country_response)
        if found:
            return found

    # 3) Fall back to a larger paginated list
    fallback_response = api_get(
        "leagues",
        token=token,
        params={"per_page": 200, "include": "country"},
    )
    found = _try_candidates(fallback_response)
    if found:
        return found

    raise LookupError(f"Could not find league named '{name}' in Sportmonks response")


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
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    try:
        token = require_token()
        country_id = None if args.country_id < 0 else args.country_id

        if args.league_id is not None:
            league_response = api_get(
                f"leagues/{args.league_id}",
                token=token,
                params={"include": "country"},
            )
            league = league_response.get("data") or {}
            if not league:
                raise LookupError(f"Could not find league with id {args.league_id}")
        else:
            league = find_league(token=token, name=args.league_name, country_id=country_id)

        league_id = league["id"]
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

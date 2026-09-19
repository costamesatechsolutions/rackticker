"""Lookups that make Settings friendly: team pickers and place search.

Both are fetched by RackTicker (not the browser), cached, and return only
what the page needs, so the page keeps its strict no-third-party policy.
"""
from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import quote

import aiohttp
from aiohttp import web

LEAGUE_PATHS = {"NFL": "football/nfl", "NCAAF": "football/college-football", "NBA": "basketball/nba",
                "NCAAM": "basketball/mens-college-basketball", "WNBA": "basketball/wnba",
                "MLB": "baseball/mlb", "NHL": "hockey/nhl"}
TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/{}/teams?limit=500"
PLACES_URL = "https://geocoding-api.open-meteo.com/v1/search?name={}&count=6&language=en&format=json"
_teams = {}


async def _json(url):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8),
                                     headers={"User-Agent": "RackTicker/0.3 (+https://github.com/costamesatechsolutions/rackticker)"}) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json(content_type=None)


async def teams(request):
    league = request.query.get("league", "").upper()
    if league not in LEAGUE_PATHS:
        raise ValueError(f"league must be one of {', '.join(LEAGUE_PATHS)}")
    cached = _teams.get(league)
    if not cached or time.monotonic() - cached[0] > 86400:
        try:
            payload = await _json(TEAMS_URL.format(LEAGUE_PATHS[league]))
            rows = [entry["team"] for entry in payload["sports"][0]["leagues"][0]["teams"]]
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, IndexError, TypeError) as exc:
            if cached:
                return web.json_response(cached[1])
            raise ValueError(f"Could not load {league} teams right now") from exc
        result = sorted(({"abbreviation": str(team.get("abbreviation") or "").upper(),
                          "name": str(team.get("displayName") or ""),
                          "short": str(team.get("shortDisplayName") or ""),
                          "color": "#" + str(team.get("color") or "666666")[:6]}
                         for team in rows if team.get("abbreviation")), key=lambda row: row["name"])
        cached = _teams[league] = (time.monotonic(), {"league": league, "teams": result})
    return web.json_response(cached[1])


async def places(request):
    query = " ".join(request.query.get("q", "").split())[:80]
    if len(query) < 2:
        return web.json_response({"places": []})
    try:
        payload = await _json(PLACES_URL.format(quote(query)))
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ValueError("Place search is unavailable right now") from exc
    found = []
    for row in payload.get("results") or []:
        parts = [row.get("name"), row.get("admin1"), row.get("country_code")]
        found.append({"name": ", ".join(str(part) for part in parts if part),
                      "latitude": round(float(row["latitude"]), 4), "longitude": round(float(row["longitude"]), 4),
                      "timezone": row.get("timezone") or ""})
    return web.json_response({"places": found})


def add_routes(app):
    app.add_routes([web.get("/api/teams", teams), web.get("/api/places", places)])

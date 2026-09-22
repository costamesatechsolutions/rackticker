"""Key-free multi-league schedules and scores for RackTicker."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from PIL import Image

from rackticker import Game, Plugin, Provider, Snapshot, Team, offload


NHL_SCHEDULE_API = "https://api-web.nhle.com/v1/schedule/{date}"
NHL_SCORE_API = "https://api-web.nhle.com/v1/score/{date}"   # today's games with shots, situation, goals
ESPN_API = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
ESPN_LEAGUES = {
    "NFL": "football/nfl",
    "NBA": "basketball/nba",
    "MLB": "baseball/mlb",
    "WNBA": "basketball/wnba",
    "NCAAF": "football/college-football",
    "NCAAM": "basketball/mens-college-basketball",
}
SUPPORTED_LEAGUES = {"NHL", *ESPN_LEAGUES}
TEAM_COLORS = {
    "ANA": "#fc4c02", "BOS": "#ffb81c", "BUF": "#003087",
    "CAR": "#cc0000", "CBJ": "#002654", "CGY": "#d2001c",
    "CHI": "#cf0a2c", "COL": "#6f263d", "DAL": "#006847",
    "DET": "#ce1126", "EDM": "#ff4c00", "FLA": "#c8102e",
    "LAK": "#a2aaad", "MIN": "#a6192e", "MTL": "#af1e2d",
    "NJD": "#ce1126", "NSH": "#ffb81c", "NYI": "#00539b",
    "NYR": "#0038a8", "OTT": "#da1a32", "PHI": "#f74902",
    "PIT": "#fcb514", "SEA": "#99d9d9", "SJS": "#006d75",
    "STL": "#002f87", "TBL": "#002868", "TOR": "#003e7e",
    "UTA": "#6cace4", "VAN": "#00205b", "VGK": "#b4975a",
    "WPG": "#041e42", "WSH": "#c8102e",
}
ESPN_NHL_CODES = {"LAK": "la", "NJD": "nj", "SJS": "sj", "TBL": "tb"}
LIVE_STATES = {"LIVE", "CRIT"}
FINAL_STATES = {"FINAL", "OFF"}


def parse_csv(value):
    return [part.strip().upper() for part in value.split(",") if part.strip()]


def game_start(raw):
    value = raw.get("startTimeUTC")
    if not isinstance(value, str):
        raise ValueError("NHL game is missing startTimeUTC")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _score(value):
    try:
        score = int(float(value or 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid score") from exc
    if not 0 <= score <= 999:
        raise ValueError("Invalid score")
    return score


def _team(abbreviation, score, color=None, logo_url=None):
    abbreviation = str(abbreviation or "---").upper()
    if not re.fullmatch(r"[A-Z0-9]{2,4}", abbreviation):
        raise ValueError("Invalid team abbreviation")
    candidate = (f"#{color}" if isinstance(color, str)
                 and re.fullmatch(r"[0-9a-fA-F]{6}", color) else None)
    logo_url = str(logo_url or "")
    if logo_url and not logo_url.startswith("https://"):
        logo_url = ""
    return Team(abbreviation[:3], _score(score),
                candidate or TEAM_COLORS.get(abbreviation, "#e8f0eb"), logo_url)


def _local_time(start, timezone_name):
    try:
        local = start.astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown sports timezone") from exc
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local:%a} {local.month}/{local.day} {hour}:{local:%M%p}".upper()


def _nhl_logo(team):
    """Use a runtime PNG because the NHL schedule supplies SVG-only marks.
    The "scoreboard" variant is ESPN's own simplified mark for small displays
    (a wordmark like the Jets' would otherwise turn to mush at panel size)."""
    abbreviation = str(team.get("abbrev") or "").upper()
    code = ESPN_NHL_CODES.get(abbreviation, abbreviation.lower())
    return f"https://a.espncdn.com/i/teamlogos/nhl/500/scoreboard/{code}.png" if code else ""


def normalize_nhl_game(raw, timezone_name):
    if not isinstance(raw, dict):
        raise ValueError("Invalid NHL game")
    state = str(raw.get("gameState") or "FUT").upper()
    start = game_start(raw)
    if state in LIVE_STATES:
        status = "live"
        descriptor = raw.get("periodDescriptor") or {}
        period = descriptor.get("number")
        clock = raw.get("clock") or {}
        remaining = str(clock.get("timeRemaining") or "").strip()
        if clock.get("inIntermission"):
            detail = f"INT {period or ''}".strip()
        else:
            period_type = str(descriptor.get("periodType") or "").upper()
            label = period_type if period_type in {"OT", "SO"} else f"P{period or '?'}"
            detail = f"{label} {remaining}".strip()
    elif state in FINAL_STATES:
        status = "final"
        period_type = str((raw.get("periodDescriptor") or {}).get("periodType") or "").upper()
        detail = "FINAL" + (f" {period_type}" if period_type in {"OT", "SO"} else "")
    else:
        status = "pregame"
        detail = _local_time(start, timezone_name)
    home, away = raw.get("homeTeam") or {}, raw.get("awayTeam") or {}
    game = Game(_team(home.get("abbrev"), home.get("score"), logo_url=_nhl_logo(home)),
                _team(away.get("abbrev"), away.get("score"), logo_url=_nhl_logo(away)),
                status, detail[:20], "NHL")
    return {"id": f"NHL:{raw.get('id')}", "game": game, "start": start}


def nhl_live(raw):
    """What a hockey broadcast shows, as display strings: shots on goal, a power
    play and its clock, an empty net, and the latest goal (scorer, kind)."""
    extra = {}
    home, away = raw.get("homeTeam") or {}, raw.get("awayTeam") or {}
    for side, team in (("home", home), ("away", away)):
        if isinstance(team.get("sog"), int):
            extra[f"{side}_sog"] = str(team["sog"])
    situation = raw.get("situation") or {}
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        described = (situation.get(key) or {}).get("situationDescriptions") or []
        if "PP" in described:
            extra["pp"] = side
            extra["pp_time"] = str(situation.get("timeRemaining") or "")[:5]
        if "EN" in described:
            extra["empty_net"] = side   # that team has pulled its goalie for an extra skater
    goals = [goal for goal in raw.get("goals") or [] if isinstance(goal, dict)]
    extra["goal_count"] = str(len(goals))
    if goals:
        last = goals[-1]
        name = str(((last.get("lastName") or {}).get("default")) or ((last.get("name") or {}).get("default")) or "")
        total = last.get("goalsToDate")
        extra["last_goal"] = f"{name.upper()}{f' ({total})' if isinstance(total, int) else ''}"[:24]
        extra["last_goal_team"] = str(last.get("teamAbbrev") or (last.get("teamAbbrev") or {}))[:4]
        kind = "EN" if last.get("goalModifier") == "empty-net" else str(last.get("strength") or "").upper()
        extra["last_goal_kind"] = {"PP": "POWER-PLAY GOAL", "SH": "SHORTHANDED GOAL", "EN": "EMPTY-NET GOAL"}.get(kind, "GOAL")
    return extra


def normalize_espn_event(raw, league, timezone_name):
    if not isinstance(raw, dict) or not raw.get("competitions"):
        raise ValueError("Invalid ESPN event")
    competition = raw["competitions"][0]
    competitors = competition.get("competitors") or []
    by_side = {item.get("homeAway"): item for item in competitors if isinstance(item, dict)}
    if "home" not in by_side or "away" not in by_side:
        raise ValueError("Score event is missing a team")
    start = datetime.fromisoformat(str(raw.get("date")).replace("Z", "+00:00"))
    status_data = competition.get("status") or raw.get("status") or {}
    kind = status_data.get("type") or {}
    state = kind.get("state")
    if state == "in":
        status = "live"
        detail = kind.get("shortDetail") or kind.get("detail") or "LIVE"
    elif state == "post" or kind.get("completed"):
        status = "final"
        detail = kind.get("shortDetail") or "FINAL"
    else:
        status = "pregame"
        detail = _local_time(start, timezone_name)

    def competitor(side):
        item, team = by_side[side], by_side[side].get("team") or {}
        logo = team.get("logo")
        if not logo and team.get("logos"):
            logo = team["logos"][0].get("href")
        return _team(team.get("abbreviation"), item.get("score"), team.get("color"), logo)

    odds = competition.get("odds") or []
    line, extra = "", {}
    if odds and isinstance(odds[0], dict):
        details = str(odds[0].get("details") or "").upper().strip()
        total = odds[0].get("overUnder")
        total_text = f"O/U {total:g}" if isinstance(total, (int, float)) else ""
        line = " ".join(part for part in (details, total_text) if part)[:20]
        extra = sportsbook_lines(odds[0])
    for side in ("home", "away"):
        records = by_side[side].get("records") or []
        summary = str(records[0].get("summary") or "") if records and isinstance(records[0], dict) else ""
        if re.fullmatch(r"\d{1,3}-\d{1,3}(-\d{1,3})?", summary):
            extra[f"{side}_record"] = summary
    broadcasts = competition.get("broadcasts") or []
    if broadcasts and isinstance(broadcasts[0], dict) and broadcasts[0].get("names"):
        extra["broadcast"] = str(broadcasts[0]["names"][0]).upper()[:16]
    situation = competition.get("situation")
    if status == "live" and isinstance(situation, dict):
        ids = {str((by_side[side].get("team") or {}).get("id")): side for side in ("home", "away")}
        extra.update(live_situation(situation, ids))
    game = Game(competitor("home"), competitor("away"), status,
                str(detail).upper()[:20], league, line, extra)
    return {"id": f"{league}:{raw.get('id')}", "game": game, "start": start}


BANNER_ONLY = {"POWER PLAY", "RED ZONE"}
FLASH_SECONDS = 180   # a big play stays on its game's card this long


def live_situation(situation, sides):
    """What a TV score bug shows, as display strings: bases, outs and count in
    baseball; down, distance, ball and red zone in football; and the last play."""
    extra = {}
    if "outs" in situation:
        extra["bases"] = "".join("1" if situation.get(key) else "0" for key in ("onFirst", "onSecond", "onThird"))
        extra["outs"] = str(max(0, min(3, int(situation.get("outs") or 0))))
        extra["count"] = f"{int(situation.get('balls') or 0)}-{int(situation.get('strikes') or 0)}"
        for role in ("batter", "pitcher"):
            name = ((situation.get(role) or {}).get("athlete") or {}).get("shortName")
            if name:
                extra[role] = str(name)[:20]
    if situation.get("shortDownDistanceText"):
        extra["down"] = str(situation["shortDownDistanceText"]).upper()[:12]
        extra["spot"] = str(situation.get("possessionText") or "").upper()[:10]
        side = sides.get(str(situation.get("possession")))
        if side:
            extra["ball"] = side
        if situation.get("isRedZone"):
            extra["red_zone"] = "1"
        # Where the ball is: yards from the home team's goal line (ESPN's convention),
        # yards to go, and timeouts left, for the field strip.
        for key, name in (("yardLine", "yard"), ("distance", "togo"), ("homeTimeouts", "home_timeouts"),
                          ("awayTimeouts", "away_timeouts")):
            if isinstance(situation.get(key), int):
                extra[name] = str(situation[key])
    play = situation.get("lastPlay") or {}
    if play.get("text"):
        extra["play"] = str(play["text"])[:160]
        extra["play_id"] = str(play.get("id") or play["text"])[:40]
    return extra


def batting_side(game):
    """Who is at bat from "TOP 8TH" / "BOT 8TH", or None between halves."""
    half = game.detail.split(" ")[0]
    return "away" if half == "TOP" else "home" if half == "BOT" else None


def play_call(game, before):
    """A big play between two looks at a live game, as (call, side that made it,
    who: the scorer or hitter, or ""), or None. Plays are read from ESPN's last-play text and, since a poll can miss
    that line, from what changed: two outs at once, the bases cleared by a homer."""
    extra, old = game.extra, before.extra
    text = extra.get("play", "").lower() if extra.get("play_id") != old.get("play_id") else ""
    if game.league == "MLB":
        batting = batting_side(game)
        fielding = {"home": "away", "away": "home"}.get(batting)
        same_half = game.detail == before.detail and batting
        if "triple play" in text:
            return "TRIPLE PLAY", fielding, ""
        if "double play" in text or (same_half and "outs" in extra and "outs" in old
                                      and int(extra["outs"]) - int(old["outs"]) == 2):
            return "DOUBLE PLAY", fielding, ""
        if batting:
            runs = getattr(game, batting).score - getattr(before, batting).score
            runners = old.get("bases", "000").count("1")
            homer = "homered" in text or "home run" in text or "grand slam" in text or (
                same_half and runs >= 1 and extra.get("bases") == "000" and runs == runners + 1)
            if homer and runs >= 1:
                return ("GRAND SLAM" if runs == 4 or "grand slam" in text else "HOME RUN"), batting, extra.get("batter", "")
    elif game.league == "NHL":
        if int(extra.get("goal_count") or 0) > int(old.get("goal_count") or 0):
            team = extra.get("last_goal_team")
            side = "home" if team == game.home.abbreviation else "away" if team == game.away.abbreviation else None
            return extra.get("last_goal_kind", "GOAL"), side, extra.get("last_goal", "")
        if extra.get("pp") and extra.get("pp") != old.get("pp"):
            return "POWER PLAY", extra["pp"], ""
    elif game.league in ("NFL", "NCAAF"):
        defense = {"home": "away", "away": "home"}.get(old.get("ball"))
        if "intercepted" in text:
            return "INTERCEPTION", defense, ""
        if "fumble" in text and extra.get("ball") and extra.get("ball") != old.get("ball"):
            return "FUMBLE", defense, ""  # lost: the other team has the ball now
        gain = re.search(r"for (\d{2,3}) yards", text)
        if gain and int(gain[1]) >= 25 and "penalty" not in text and old.get("ball"):
            return f"BIG PLAY +{gain[1]}", old["ball"], ""
        if extra.get("red_zone") and not old.get("red_zone") and extra.get("ball"):
            return "RED ZONE", extra["ball"], ""
    return None


def score_call(league, points):
    """What the arena announcer would shout, or None for sports that score constantly."""
    if points <= 0:
        return None
    if league in ("NFL", "NCAAF"):
        return {6: "TOUCHDOWN", 7: "TOUCHDOWN", 8: "TOUCHDOWN", 3: "FIELD GOAL", 2: "SAFETY"}.get(points, "SCORE")
    if league == "NHL":
        return "GOAL" if points == 1 else f"{points} GOALS"
    if league == "MLB":
        return "RUN SCORES" if points == 1 else f"{points} RUNS SCORE"
    return None


def sportsbook_lines(odds):
    """Closing (current) and opening spread, total and moneyline as display strings."""
    def pick(market, side, stage, key):
        value = (((odds.get(market) or {}).get(side) or {}).get(stage) or {}).get(key)
        text = str(value or "").strip().upper()
        return text if re.fullmatch(r"[+-]?(\d{1,4}(\.\d)?|PK|EVEN)", text) else ""
    lines = {}
    for side in ("home", "away"):
        lines[f"{side}_ml"] = pick("moneyline", side, "close", "odds")
        lines[f"{side}_spread"] = pick("pointSpread", side, "close", "line")
        lines[f"{side}_spread_open"] = pick("pointSpread", side, "open", "line")
    total = odds.get("overUnder")
    lines["total"] = f"{total:g}" if isinstance(total, (int, float)) else ""
    provider = (odds.get("provider") or {}).get("name")
    if provider:
        lines["book"] = "".join(word[0] for word in str(provider).upper().split())[:3]
    return {key: value for key, value in lines.items() if value}


# ESPN and the NHL's own feed spell a few teams differently; the team picker
# stores ESPN's, so a favourite matches either spelling.
ALIASES = {"LA": "LAK", "SJ": "SJS", "TB": "TBL", "NJ": "NJD", "UTAH": "UTA", "MON": "MTL", "WAS": "WSH"}


def with_aliases(teams):
    teams = set(teams)
    for short, long in ALIASES.items():
        if short in teams or long in teams:
            teams |= {short, long}
    return teams


def rotation(games, favorites, now, timezone_name="UTC"):
    """Build a league-wide sportsbook slate without injecting future favorites."""
    values = list({item["id"]: item for item in games}.values())
    zone = ZoneInfo(timezone_name)
    today = now.astimezone(zone).date()
    local_day = lambda item: item["start"].astimezone(zone).date()
    live = [item for item in values if item["game"].status == "live"]
    current = [item for item in values if local_day(item) == today]
    # When a league is idle today, include its entire nearest future game day.
    # This gives NHL fans the full next slate rather than season-long ANA/LAK
    # schedule entries, while MLB/NFL/NBA can coexist on the same board.
    future_slates, later = [], []
    for league in sorted({item["game"].league for item in values}):
        league_future = [item for item in values
                         if item["game"].league == league and local_day(item) > today]
        if league_future:
            next_day = min(local_day(item) for item in league_future)
            slate = [item for item in league_future if local_day(item) == next_day]
            # Tomorrow earns a place on the board; a slate days away is only filler.
            (future_slates if (next_day - today).days <= 1 else later).extend(slate)
    # Mornings and quiet days lead with last night's finals, which beat filler
    # games days away.
    recent = [item for item in values
              if item["game"].status == "final" and local_day(item) == today - timedelta(days=1)]
    morning = now.astimezone(zone).hour < 12
    ordered = live + current + (recent if morning or not current else []) + future_slates
    if not ordered:
        ordered = sorted(later, key=lambda item: item["start"])[:6]
    result, seen = [], set()
    for item in sorted(ordered, key=lambda item: (
            item["game"].status != "live",
            local_day(item) != today,
            item["start"],
            not (local_day(item) == today and favorites.intersection(
                 {item["game"].home.abbreviation,
                  item["game"].away.abbreviation})),
            item["id"])):
        if item["id"] not in seen:
            result.append(item)
            seen.add(item["id"])
    if not result and values:
        result = [min(values, key=lambda item: abs((item["start"] - now).total_seconds()))]
    return result[:64]


def parse_scoreboards(feeds, timezone_name):
    """(kind, league, raw JSON bytes) per feed -> normalized games."""
    games = []
    for kind, league, raw in feeds:
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if kind == "nhl":
            rows = [game for day in payload.get("gameWeek", []) if isinstance(day, dict)
                    for game in day.get("games", [])]
        elif kind == "nhl-score":
            rows = payload.get("games", [])
        else:
            rows = payload.get("events", [])
        for event in rows if isinstance(rows, list) else []:
            try:
                if kind == "nhl-score":   # today's games again, with shots, situation and goals
                    item = normalize_nhl_game(event, timezone_name)
                    item["game"] = replace(item["game"], extra={**item["game"].extra, **nhl_live(event)})
                    games.append(item)
                    continue
                games.append(normalize_nhl_game(event, timezone_name) if kind == "nhl"
                             else normalize_espn_event(event, league, timezone_name))
            except (ValueError, TypeError, KeyError):
                continue
    return games


LOGO_SIZE = 20   # big enough that thin marks (a wordmark, a script logo) survive the downscale


def shrink_team_logo(raw):
    """A crisp LOGO_SIZE×LOGO_SIZE PNG mark from a full-size logo."""
    if len(raw) > 600_000:
        raise ValueError("Logo is too large")
    # Premultiplied box downscaling avoids dark fringes, and a hard alpha
    # edge keeps marks crisp instead of muddy half-lit pixels.
    source = Image.open(BytesIO(raw)).convert("RGBA").convert("RGBa")
    source.thumbnail((LOGO_SIZE, LOGO_SIZE), Image.Resampling.BOX)
    source = source.convert("RGBA")
    source.putalpha(source.getchannel("A").point(lambda alpha: 255 if alpha >= 110 else 0))
    image = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE))
    image.alpha_composite(source, ((LOGO_SIZE - source.width) // 2, (LOGO_SIZE - source.height) // 2))
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


class FreeSports(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.cached_games = []
        self.cache_until = 0.0
        self.logo_cache = {}
        self.previous = {}    # game id -> the game as last seen
        self.flashes = {}     # game id -> the latest big play, shown on that game's card
        self.celebration = None

    def _celebrate(self, call, team, other, league, who=""):
        score = f"{team.abbreviation} {team.score}  {other.abbreviation} {other.score}"
        self.celebration = {"call": call, "team": team.abbreviation, "color": team.color,
                            "league": league, "at": time.monotonic(),
                            "line": f"{who}  {score}" if who else score}
        if not self.context.emit_event("sportsbook", 12):
            self.context.emit_event("sports", 12)

    def _check_plays(self, favorites):
        """Big plays in live games (fresh data only): every game's card shows its
        latest one for a while, and your teams' take over the panel with fireworks."""
        latest, now = {}, time.time()
        for item in self.cached_games:
            game = item["game"]
            latest[item["id"]] = game
            before = self.previous.get(item["id"])
            if not before or game.status != "live":
                continue
            teams = {"home": (game.home, game.away), "away": (game.away, game.home)}
            play = play_call(game, before)
            if play and play[1]:
                call, side, who = play
                team, other = teams[side]
                self.flashes[item["id"]] = {"call": call, "team": team.abbreviation, "who": who, "at": now}
                # Your team's goals, homers and turnovers take over the panel; momentum
                # (a power play, the red zone, a long gain) is a banner on the card.
                if team.abbreviation in favorites and call not in BANNER_ONLY and not call.startswith("BIG PLAY"):
                    self._celebrate(call, team, other, game.league, who)
                    continue
            for side, (team, other) in teams.items():
                call = score_call(game.league, team.score - getattr(before, side).score)
                if call and team.abbreviation in favorites and not (play and play[1] == side):
                    self._celebrate(call, team, other, game.league)
        self.previous = latest
        self.flashes = {key: flash for key, flash in self.flashes.items() if now - flash["at"] < FLASH_SECONDS}
        for item in self.cached_games:
            flash = self.flashes.get(item["id"])
            if flash:
                extra = {**item["game"].extra, "flash": flash["call"], "flash_team": flash["team"],
                         "flash_who": flash.get("who", ""), "flash_at": f"{flash['at']:.0f}"}
                item["game"] = replace(item["game"], extra=extra)

    async def _raw(self, url):
        # A scoreboard is a big document and the Pi's Wi-Fi is slow; the session's
        # short default is for logos. The provider as a whole gets 6 s, and logos
        # after this take at most 1.7.
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as response:
            response.raise_for_status()
            return await response.read()

    async def _fetch_all(self, leagues, favorites):
        requests, kinds = [], []
        if "NHL" in leagues:
            local_date = datetime.now(ZoneInfo(self.context.settings["timezone"])).date()
            requests.append(self._raw(NHL_SCHEDULE_API.format(date=local_date.isoformat())))
            kinds.append(("nhl", None))
            requests.append(self._raw(NHL_SCORE_API.format(date=local_date.isoformat())))
            kinds.append(("nhl-score", None))
        for league in sorted(leagues & ESPN_LEAGUES.keys()):
            requests.append(self._raw(ESPN_API.format(path=ESPN_LEAGUES[league])))
            kinds.append(("espn", league))
        responses = await asyncio.gather(*requests, return_exceptions=True)
        feeds = [(kind, league, payload) for (kind, league), payload in zip(kinds, responses)
                 if not isinstance(payload, Exception)]
        # Scoreboards are large JSON documents: parse them off the render loop.
        games = await offload(parse_scoreboards, feeds, self.context.settings["timezone"])
        if not games:
            raise ConnectionError("No enabled sports feed returned usable games")
        return games

    async def _logo(self, url):
        if not url:
            return b""
        if url in self.logo_cache:
            return self.logo_cache[url]
        try:
            raw = None
            # ESPN's dark-background marks (gold Padres, white Yankees) read on
            # a black LED panel; the default marks are drawn for white pages.
            for candidate in dict.fromkeys((url.replace("/500/", "/500-dark/"), url)):
                try:
                    async with self.session.get(candidate) as response:
                        response.raise_for_status()
                        raw = await response.read()
                    break
                except aiohttp.ClientError:
                    continue
            if raw is None:
                raise ValueError("Logo unavailable")
            packed = await offload(shrink_team_logo, raw)
        except (aiohttp.ClientError, OSError, ValueError):
            packed = b""
        if len(self.logo_cache) >= 256:
            self.logo_cache.clear()
        self.logo_cache[url] = packed
        return packed

    async def _logos(self, game):
        if not self.context.settings["show_logos"]:
            return game
        home, away = await asyncio.gather(self._logo(game.home.logo_url),
                                          self._logo(game.away.logo_url))
        return replace(game, home=replace(game.home, logo_png=home),
                       away=replace(game.away, logo_png=away))

    async def _slate_logos(self, games):
        """Attach cached logos across the slate, fetching only a few new images
        per poll so a cold cache never stalls the provider deadline."""
        if not self.context.settings["show_logos"]:
            return tuple(games)
        wanted = [url for game in games for url in (game.away.logo_url, game.home.logo_url)
                  if url and url not in self.logo_cache]
        await asyncio.gather(*(self._logo(url) for url in list(dict.fromkeys(wanted))[:8]))

        def attach(team):
            return replace(team, logo_png=self.logo_cache.get(team.logo_url, b"")) if team.logo_url else team
        return tuple(replace(game, home=attach(game.home), away=attach(game.away)) for game in games)

    async def fetch(self):
        settings = self.context.settings
        leagues = set(parse_csv(settings["leagues"]))
        favorites = with_aliases(parse_csv(settings["favorite_teams"]))
        tick = time.monotonic()
        if tick >= self.cache_until or not self.cached_games:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=1.7),
                    headers={"User-Agent": "RackTicker/0.1 (+https://github.com/costamesatechsolutions/rackticker)"},
                )
            self.cached_games = await self._fetch_all(leagues, favorites)
            self.cache_until = tick + settings["refresh_seconds"]
            self._check_plays(favorites)
        now = datetime.now(timezone.utc)
        games = rotation(self.cached_games, favorites, now, settings["timezone"])
        if not games:
            raise ValueError("No current or upcoming games")
        selected = games[int(time.time() // settings["cycle_seconds"]) % len(games)]
        game = await self._logos(selected["game"])
        return Snapshot(game, updated_at=now, source="free_sports",
                        metadata={"game_id": selected["id"],
                                  "rotation_size": len(games),
                                  "slate": await self._slate_logos([item["game"] for item in games]),
                                  "celebration": self.celebration})

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()


def validate(settings):
    leagues = (parse_csv(settings.get("leagues", ""))
               if isinstance(settings.get("leagues"), str) else [])
    if not leagues or len(leagues) != len(set(leagues)) or set(leagues) - SUPPORTED_LEAGUES:
        raise ValueError("leagues contains an unsupported or duplicate league")
    favorites = (parse_csv(settings.get("favorite_teams", ""))
                 if isinstance(settings.get("favorite_teams"), str) else [])
    if len(favorites) > 32 or any(not re.fullmatch(r"[A-Z0-9]{2,4}", team)
                                  for team in favorites):
        raise ValueError("favorite_teams must be comma-separated team abbreviations")
    for key, low, high in (("refresh_seconds", 15, 300),
                           ("cycle_seconds", 6, 60)):
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{key} must be {low}–{high}")
    timezone_name = settings.get("timezone")
    if not isinstance(timezone_name, str):
        raise ValueError("timezone must be an IANA timezone name")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be an IANA timezone name") from exc
    if not isinstance(settings.get("show_logos"), bool):
        raise ValueError("show_logos must be true or false")


plugin = Plugin(
    "free_sports", "Free sports scores", provider=FreeSports,
    provider_for="sports",
    defaults={"leagues": "NHL,NFL,MLB,NBA",
              "favorite_teams": "ANA,LAK,SD", "refresh_seconds": 30,
              "cycle_seconds": 8, "timezone": "America/Los_Angeles",
              "show_logos": True},
    validate_settings=validate,
    help={"leagues": "Any of NFL, NCAAF, NBA, NCAAM, WNBA, MLB, NHL",
          "favorite_teams": "Team abbreviations; their scores trigger celebrations"},
    ui={"leagues": {"type": "multi", "options": ["NFL", "NCAAF", "NBA", "NCAAM", "WNBA", "MLB", "NHL"]}, "favorite_teams": {"type": "teams", "leagues": "leagues", "label": "Your teams"}, "cycle_seconds": {"type": "slider", "min": 3, "max": 30, "unit": "s", "label": "Seconds per game"}, "refresh_seconds": {"advanced": True}, "timezone": {"advanced": True, "label": "Time zone"}})

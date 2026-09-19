"""Now Playing: the song you are listening to, like an old car stereo.

Album art on the left, the title and artist scrolling like a head unit's
display, the track number and time, and a segmented spectrum analyser with
peak caps. From Spotify (your own free developer app) or from any Home Assistant
media player (Spotify, Sonos, Apple TV, an Echo…).

Neither Spotify nor Home Assistant publishes the audio itself, so the analyser
is a drummer, not a microphone: it plays a beat at a tempo picked for each song
and follows the song's real progress, pausing when the music does.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import io
import json
import math
from pathlib import Path
import random
import re
import time

import aiohttp
from PIL import Image, ImageDraw

from rackticker import Module, Plugin, Provider, Snapshot, draw_text, draw_tiny, new_frame, offload, text_width, tiny_width

SPOTIFY_TOKEN = "https://accounts.spotify.com/api/token"
SPOTIFY_PLAYING = "https://api.spotify.com/v1/me/player/currently-playing?additional_types=episode"
# Where the plugin keeps Spotify's newest refresh token: Spotify replaces it on every
# refresh, and an installed plugin's home is its own data folder.
TOKEN_FILE = ".now-playing-spotify.json"
PAUSED_SECONDS = 120   # stay on screen this long after the music stops
LRCLIB = "https://lrclib.net/api/get"
LYRIC_LINGER = 8.0     # a sung line stays this long; after that it is an instrumental stretch

WHITE, GREY, DIM = (236, 238, 236), (150, 156, 160), (50, 54, 58)
GREEN, YELLOW, RED = (60, 220, 90), (255, 200, 30), (255, 50, 40)
BAR_W, BAR_GAP = 5, 1
TEXT_X, RIGHT = 35, 127
INFO_SECONDS, EQ_SECONDS = 9.0, 5.0   # the display cycles: song, then the full analyser


# --- sources ------------------------------------------------------------------------

def prepare_art(raw):
    """Album art as a 32×32 RGB tile plus its strongest colour (runs in a helper process)."""
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    tile = image.resize((32, 32), Image.Resampling.LANCZOS)
    small = image.resize((16, 16), Image.Resampling.BILINEAR)
    best, accent = 0, None
    for r, g, b in small.getdata():
        high, low = max(r, g, b), min(r, g, b)
        score = (high - low) * high  # saturated and bright: readable as text on black
        if score > best and high > 90:
            best, accent = score, (r, g, b)
    if accent and best > 60 * 150:
        scale = 255 / max(accent)
        accent = tuple(round(c * scale) for c in accent)
        if accent[0] > 200 and accent[2] > 150 and accent[1] < 120:  # no pink on LEDs
            accent = None
        elif .3 * accent[0] + .59 * accent[1] + .11 * accent[2] < 110:  # deep blue: lift it to read
            accent = tuple(round(c + (255 - c) * .35) for c in accent)
    else:
        accent = None
    return tile.tobytes(), accent


async def read_all(response, limit):
    """The whole body up to `limit` bytes (`content.read(n)` stops at what has arrived)."""
    data = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        data += chunk
        if len(data) >= limit:
            break
    return bytes(data[:limit])


class Spotify:
    def __init__(self, session):
        self.session = session
        self.access, self.expires = None, 0.0

    def _stored(self, seed):
        path = Path.home() / TOKEN_FILE
        try:
            saved = json.loads(path.read_text())
            if saved.get("seed") == seed and saved.get("token"):
                return saved["token"]
        except (OSError, ValueError):
            pass
        return seed

    @staticmethod
    def _store(seed, token):
        path = Path.home() / TOKEN_FILE
        path.write_text(json.dumps({"seed": seed, "token": token}))
        path.chmod(0o600)

    async def _token(self, settings):
        if self.access and time.time() < self.expires - 60:
            return self.access
        seed = settings["spotify_refresh_token"].strip()
        client = settings["spotify_client_id"].strip()
        if not seed or not client:
            raise ValueError("Connect Spotify: run spotify_login.py from this plugin's folder (see its README)")
        form = {"grant_type": "refresh_token", "refresh_token": self._stored(seed), "client_id": client}
        async with self.session.post(SPOTIFY_TOKEN, data=form) as response:
            payload = await response.json(content_type=None)
            if response.status != 200:
                raise ValueError(f"Spotify login expired ({payload.get('error', response.status)}): "
                                 "run spotify_login.py again")
        if payload.get("refresh_token"):
            self._store(seed, payload["refresh_token"])
        self.access, self.expires = payload["access_token"], time.time() + int(payload.get("expires_in", 3600))
        return self.access

    async def playing(self, settings):
        token = await self._token(settings)
        async with self.session.get(SPOTIFY_PLAYING, headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status == 401:
                self.access = None
            if response.status in (204, 202):
                return None
            response.raise_for_status()
            payload = await response.json(content_type=None)
        item = payload.get("item")
        if not item:
            return None
        if item.get("type") == "episode":
            artist, album = (item.get("show") or {}).get("name", ""), (item.get("show") or {}).get("name", "")
            images = item.get("images") or (item.get("show") or {}).get("images") or []
        else:
            artist = ", ".join(a["name"] for a in item.get("artists") or [] if a.get("name"))
            album = (item.get("album") or {}).get("name", "")
            images = (item.get("album") or {}).get("images") or []
        # The smallest picture at least 64 px wide: plenty for 32 LEDs.
        images = sorted((i for i in images if i.get("url")), key=lambda i: i.get("width") or 640)
        art = next((i["url"] for i in images if (i.get("width") or 640) >= 64), images[-1]["url"] if images else None)
        return {"id": item.get("id") or item.get("name"), "title": item.get("name", ""), "artist": artist,
                "album": album, "track": item.get("track_number"), "playing": bool(payload.get("is_playing")),
                "progress": (payload.get("progress_ms") or 0) / 1000, "duration": (item.get("duration_ms") or 0) / 1000,
                "art": art, "headers": {}}


class HomeAssistant:
    def __init__(self, session):
        self.session = session

    async def playing(self, settings):
        base, token = settings["ha_url"].strip().rstrip("/"), settings["ha_token"].strip()
        if not base or not token:
            raise ValueError("Set your Home Assistant address and a long-lived access token")
        headers = {"Authorization": f"Bearer {token}"}
        async with self.session.get(f"{base}/api/states", headers=headers) as response:
            if response.status == 401:
                raise ValueError("Home Assistant refused the token")
            response.raise_for_status()
            states = await response.json(content_type=None)
        wanted = settings["ha_entity"].strip()
        players = [s for s in states if s.get("entity_id", "").startswith("media_player.")
                   and (not wanted or s["entity_id"] == wanted)]
        # The one playing, else one paused with a song loaded.
        player = next((s for s in players if s.get("state") == "playing"), None) or \
            next((s for s in players if s.get("state") == "paused" and s["attributes"].get("media_title")), None)
        if not player:
            return None
        a = player["attributes"]
        progress = float(a.get("media_position") or 0)
        if player["state"] == "playing" and a.get("media_position_updated_at"):
            try:
                since = datetime.fromisoformat(a["media_position_updated_at"]).timestamp()
                progress += max(0.0, time.time() - since)
            except ValueError:
                pass
        picture = a.get("entity_picture") or ""
        return {"id": a.get("media_content_id") or a.get("media_title"), "title": a.get("media_title") or "",
                "artist": a.get("media_artist") or a.get("app_name") or "", "album": a.get("media_album_name") or "",
                "track": a.get("media_track"), "playing": player["state"] == "playing", "progress": progress,
                "duration": float(a.get("media_duration") or 0),
                "art": (base + picture if picture.startswith("/") else picture) or None,
                "headers": headers if picture.startswith("/") else {}}


def synced_lines(text):
    """[(seconds, line)] from LRC text: "[00:37.66]Waiting in a car"."""
    lines = []
    for raw in str(text or "").splitlines():
        match = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)", raw.strip())
        if match:
            lines.append((int(match[1]) * 60 + float(match[2]), match[3].strip()))
    return sorted(lines)


def current_line(lines, position):
    """The line being sung at `position`, when it was sung, and when the next begins."""
    sung = [(start, text) for start, text in lines if start <= position]
    if not sung or not sung[-1][1]:
        return None
    start, text = sung[-1]
    later = [begin for begin, _ in lines if begin > start]
    end = min(later[0] if later else start + LYRIC_LINGER, start + LYRIC_LINGER)
    return (text, start, end) if position < end else None


class Player(Provider):
    def __init__(self, context):
        self.context = context
        self.session = None
        self.sources = {}
        self.art = {}          # url -> (tile bytes, accent)
        self.lyrics = {}       # song id -> [(seconds, line)], or [] when none are published
        self.last_playing = 0.0

    async def fetch(self):
        settings = self.context.settings
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4),
                                                 headers={"User-Agent": "RackTicker now playing"})
            self.sources = {"spotify": Spotify(self.session), "home_assistant": HomeAssistant(self.session)}
        song = await self.sources[settings["source"]].playing(settings)
        now = time.time()
        if song and song["playing"]:
            self.last_playing = now
        if not song or now - self.last_playing > PAUSED_SECONDS:
            return Snapshot(None)
        url = song.pop("art")
        headers = song.pop("headers")
        if url and url not in self.art:
            try:
                async with self.session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    self.art = {url: await offload(prepare_art, await read_all(response, 2_000_000))}
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
                print(f"album art: {exc}")
                self.art = {url: (None, None)}
        tile, accent = self.art.get(url, (None, None))
        key = song.get("id") or song["title"]
        if settings["lyrics"] and key not in self.lyrics:
            self.lyrics = {key: await self._lyrics(song)}  # one song at a time
        return Snapshot({**song, "at": now, "tile": tile, "accent": accent,
                         "lyrics": self.lyrics.get(key, []) if settings["lyrics"] else []})

    async def _lyrics(self, song):
        """Time-synced lyrics from LRCLIB, the free open lyrics database, or []."""
        params = {"track_name": song["title"], "artist_name": song["artist"].split(",")[0].strip()}
        if song.get("album"):
            params["album_name"] = song["album"]
        if song.get("duration"):
            params["duration"] = str(round(song["duration"]))
        try:
            async with self.session.get(LRCLIB, params=params) as response:
                if response.status == 404:
                    return []
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            print(f"lyrics: {exc}")
            return []
        return synced_lines(payload.get("syncedLyrics") if isinstance(payload, dict) else "")

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# --- drawing ------------------------------------------------------------------------

def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60}:{seconds % 60:02d}"


def marquee(frame, text, x, y, width, color, t):
    """Text that fits sits still; longer text scrolls through like a head unit's
    display, resting at the start each time round."""
    full = text_width(text, 1, True)
    if full <= width:
        draw_text(frame, text, x, y, color, mixed=True)
        return
    gap, speed, rest = 18, 24, 2.0
    cycle = rest + (full + gap) / speed
    phase = t % cycle
    shift = 0 if phase < rest else round((phase - rest) * speed)
    strip = Image.new("RGB", (width, 9))
    draw_text(strip, text, -shift, 0, color, mixed=True)
    draw_text(strip, text, full + gap - shift, 0, color, mixed=True)
    frame.paste(strip, (x, y))


def record(frame, t, playing):
    """No album art: a spinning record."""
    draw = ImageDraw.Draw(frame)
    draw.ellipse((1, 1, 30, 30), fill=(18, 18, 20), outline=(60, 62, 66))
    for radius in (11, 8):
        draw.ellipse((16 - radius, 16 - radius, 15 + radius, 15 + radius), outline=(34, 34, 38))
    draw.ellipse((11, 11, 20, 20), fill=(200, 40, 30))
    angle = t * 3.5 if playing else 0
    draw.point((round(15.5 + 5 * math.cos(angle)), round(15.5 + 5 * math.sin(angle))), fill=WHITE)
    draw.point((15, 15), fill=(0, 0, 0))


class Analyser:
    """Ten bands driven by a beat: kick in the bass, snare on two and four in the
    mids, hats on the eighths up top, and a little life everywhere. Bars jump up
    and fall back; the caps hang for a moment before they drop."""

    def __init__(self, bars, segments):
        self.bars, self.segments = bars, segments
        self.levels = [0.0] * bars
        self.peaks = [0.0] * bars
        self.held = [0.0] * bars
        self.last = None

    def step(self, t, beat, playing, song_seed):
        dt = 1 / 30 if self.last is None or t < self.last else min(.2, t - self.last)
        self.last = t
        rng = random.Random(song_seed)
        wobble = [(rng.uniform(.7, 1.9), rng.uniform(0, 6.3)) for _ in range(self.bars)]
        phase = beat % 1
        kick = math.exp(-phase * 6)
        snare = math.exp(-phase * 7) if int(beat) % 2 == 1 else 0
        hat = math.exp(-((beat * 2) % 1) * 9)
        for band in range(self.bars):
            if playing:
                share = band / (self.bars - 1)
                speed, offset = wobble[band]
                life = .5 + .5 * math.sin(t * speed * 3 + offset) * math.sin(t * speed * 1.3 + offset * 2)
                target = (.12 + .7 * (1 - share) * kick + .55 * math.exp(-((share - .5) ** 2) * 12) * snare
                          + .45 * share * hat + .3 * life * (.6 + .4 * share))
                target = min(1.0, target * (1.05 - .3 * share))
            else:
                target = 0.0
            # Up at once, down smoothly, as a real analyser's ballistics.
            self.levels[band] = target if target > self.levels[band] else max(target, self.levels[band] - 1.3 * dt)
            if self.levels[band] >= self.peaks[band]:
                self.peaks[band], self.held[band] = self.levels[band], .3
            elif self.held[band] > 0:
                self.held[band] -= dt
            else:
                self.peaks[band] = max(self.levels[band], self.peaks[band] - 1.8 * dt)

    def draw(self, frame, x, bottom):
        """Segments one LED tall with a dark line between: green, then yellow, then red."""
        draw = ImageDraw.Draw(frame)
        for band in range(self.bars):
            left = x + band * (BAR_W + BAR_GAP)
            lit = round(self.levels[band] * self.segments)
            for segment in range(lit):
                share = segment / self.segments
                color = GREEN if share < .5 else YELLOW if share < .8 else RED
                y = bottom - segment * 2
                draw.line((left, y, left + BAR_W - 1, y), fill=color)
            cap = round(self.peaks[band] * self.segments)
            if cap > lit:
                y = bottom - (cap - 1) * 2
                draw.line((left, y, left + BAR_W - 1, y), fill=WHITE)


class NowPlaying(Module):
    name = "now_playing"

    def __init__(self):
        self.small = Analyser(10, 6)     # under the song
        self.full = Analyser(15, 16)     # the whole display
    def refresh_interval(self, context):
        return 1 / context.config["display"]["fps"]

    def available(self, context):
        snap = context.snapshots.get(self.name)
        return bool(snap and snap.data and snap.data.get("title"))

    def render(self, context):
        frame = new_frame()
        snap = context.snapshots.get(self.name)
        song = snap.data if snap and snap.data else None
        if not song:
            draw_text(frame, "Now playing", 2, 12, GREY, mixed=True)
            return frame
        t = context.animation_time
        playing = song["playing"]
        position = song["progress"] + (max(0.0, context.now.timestamp() - song["at"]) if playing else 0)
        if song["duration"]:
            position = min(position, song["duration"])
        accent = tuple(song["accent"]) if song.get("accent") else GREY

        if song.get("tile"):
            frame.paste(Image.frombytes("RGB", (32, 32), song["tile"]), (0, 0))
        else:
            record(frame, t, playing)

        seed = sum(ord(c) for c in str(song.get("id") or song["title"]))
        beat = position * (84 + seed % 56) / 60   # each song gets its own tempo
        self.small.step(t, beat, playing, seed)
        self.full.step(t, beat, playing, seed)
        lyric = current_line(song.get("lyrics") or [], position) if playing else None
        # While paused the display stays on the song, as a head unit does. With lyrics the
        # analyser takes over only between sung lines, so no line is missed.
        if song.get("lyrics") and playing:
            if not lyric and position > 5:
                self.full.draw(frame, TEXT_X + 1, 31)
                return frame
        elif playing and t % (INFO_SECONDS + EQ_SECONDS) >= INFO_SECONDS:
            self.full.draw(frame, TEXT_X + 1, 31)
            return frame

        width = RIGHT - TEXT_X + 1
        marquee(frame, song["title"], TEXT_X, 0, width, WHITE, t % (INFO_SECONDS + EQ_SECONDS))
        marquee(frame, song["artist"], TEXT_X, 9, width, accent, t % (INFO_SECONDS + EQ_SECONDS) + 1.3)

        # Progress: a thin rail with the played part lit.
        draw = ImageDraw.Draw(frame)
        draw.line((TEXT_X, 19, RIGHT, 19), fill=DIM)
        if song["duration"]:
            done = TEXT_X + round((RIGHT - TEXT_X) * position / song["duration"])
            if done > TEXT_X:
                draw.line((TEXT_X, 19, done, 19), fill=accent)
        if lyric:
            # The line being sung, scrolled just fast enough to finish before the next one.
            text, start, end = lyric
            full = text_width(text, 1, True)
            extra = max(0, full - width)
            shift = 0 if not extra else round(extra * min(1.0, max(0.0, (position - start - .6) / max(.5, end - start - 1.4))))
            strip = Image.new("RGB", (width, 9))
            draw_text(strip, text, -shift, 0, WHITE, mixed=True)
            frame.paste(strip, (TEXT_X, 21))
            return frame
        self.small.draw(frame, TEXT_X, 31)

        # Head-unit readout: track number over the time, which blinks while paused.
        stamp = clock(position)
        if playing or math.floor(t * 2) % 2 == 0:
            draw_text(frame, stamp, RIGHT - text_width(stamp) + 1, 25, WHITE)
        label = f"TRACK {int(song['track'])}" if song.get("track") else ""
        label = label if playing else "PAUSE"
        if label and tiny_width(label) <= RIGHT - (TEXT_X + 10 * (BAR_W + BAR_GAP)):
            draw_tiny(frame, label, RIGHT - tiny_width(label) + 1, 20, GREY)
        return frame


def validate(settings):
    url = settings["ha_url"].strip()
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("The Home Assistant address starts with http:// or https://")
    entity = settings["ha_entity"].strip()
    if entity and not entity.startswith("media_player."):
        raise ValueError("The player is a media_player entity, like media_player.living_room")


plugin = Plugin(
    "now_playing", "Now playing", module=NowPlaying, provider=Player,
    defaults={"source": "spotify", "lyrics": True, "spotify_client_id": "", "spotify_refresh_token": "",
              "ha_url": "http://homeassistant.local:8123", "ha_token": "", "ha_entity": ""},
    choices={"source": ("spotify", "home_assistant")},
    help={"source": "Spotify directly, or whatever a Home Assistant media player is playing",
          "spotify_client_id": "From your app at developer.spotify.com; spotify_login.py fills both Spotify fields",
          "spotify_refresh_token": "Written by spotify_login.py",
          "ha_token": "Home Assistant → your profile → Security → Long-lived access tokens",
          "ha_entity": "Leave empty to follow whichever player is playing",
          "lyrics": "Time-synced lyrics from LRCLIB, when the song has them"},
    ui={"spotify_client_id": {"label": "Spotify client ID"},
        "spotify_refresh_token": {"type": "secret", "label": "Spotify login"},
        "ha_url": {"label": "Home Assistant address"},
        "ha_token": {"type": "secret", "label": "Home Assistant token"},
        "ha_entity": {"label": "Player", "advanced": True}},
    validate_settings=validate,
)

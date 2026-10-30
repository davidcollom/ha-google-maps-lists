"""Read publicly shared lists. Google does not document this endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import re
from dataclasses import asdict, dataclass
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import aiohttp

HOSTS = {"www.google.com", "maps.google.com", "maps.app.goo.gl", "goo.gl"}
MAX_BYTES = 8 * 1024 * 1024


class ListError(Exception):
    """A list cannot be safely imported."""


def validate_url(url: str) -> str:
    """Allow only HTTPS Google Maps URLs, including every redirect."""
    try:
        parsed = urlsplit(url.strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname not in HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not parsed.path.startswith(("/maps", "/placelists/"))
            and parsed.hostname not in {"maps.app.goo.gl", "goo.gl"}
        ):
            raise ValueError
    except ValueError as err:
        raise ListError("invalid_url") from err
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ""))


@dataclass(frozen=True)
class Place:
    """A stable Google place or a dropped pin."""

    key: str
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class SavedList:
    """An entirely validated list snapshot."""

    key: str
    name: str
    places: tuple[Place, ...]

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> SavedList:
        return cls(value["key"], value["name"], tuple(Place(**p) for p in value["places"]))


def parse_response(raw: str, key: str) -> SavedList:
    """Reject incomplete/changed schemas rather than interpreting them as deletions."""
    if raw.startswith(")]}'"):
        raw = raw[4:].lstrip()
    try:
        root = json.loads(raw)[0]
        name, items = root[4], root[8]
        if not isinstance(name, str) or not name.strip() or not isinstance(items, list):
            raise ValueError
        # The undocumented endpoint can cap results. Do not claim a complete sync.
        if len(items) >= 500:
            raise ListError("list_too_large")
        places = {}
        for item in items:
            title, info = item[2], item[1]
            lat, lon = info[5][2:4]
            if (
                not isinstance(title, str)
                or not title.strip()
                or type(lat) not in (int, float)
                or type(lon) not in (int, float)
                or not math.isfinite(lat)
                or not math.isfinite(lon)
                or not -90 <= lat <= 90
                or not -180 <= lon <= 180
            ):
                raise ValueError
            place_id = info[7] if len(info) > 7 else None
            identity = (
                place_id if isinstance(place_id, str) and place_id else f"pin:{lat:.7f},{lon:.7f}"
            )
            place = Place(identity, title.strip(), float(lat), float(lon))
            if identity in places and places[identity] != place:
                raise ValueError
            places[identity] = place
        return SavedList(key, name.strip(), tuple(places.values()))
    except (ValueError, TypeError, IndexError, KeyError) as err:
        raise ListError("invalid_response") from err


class MapsClient:
    """Use HA's shared session without storing Google cookies or credentials."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def _get(self, url: str, *, browser: bool = False) -> tuple[str, str]:
        for _ in range(6):
            url = validate_url(url)
            headers = {
                "User-Agent": "Mozilla/5.0" if browser else "GoogleMapsLists/0.1",
                # Google's non-essential-cookie rejection preference, not authentication.
                "Cookie": "SOCS=CAI",
            }
            async with self.session.get(
                url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=30), headers=headers
            ) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ListError("invalid_response")
                return url, body.decode("utf-8")
        raise ListError("cannot_connect")

    async def fetch(self, url: str) -> SavedList:
        try:
            async with asyncio.timeout(60):
                resolved, page = await self._get(url)
                path = urlsplit(resolved).path
                match = re.search(r"/placelists/list/([^/]+)", path)
                if not match:
                    match = re.search(r"!11m\d+!2s([^!/?]+)", unquote(resolved))
                if not match:
                    raise ListError("not_shared_list")
                key = hashlib.sha256(unquote(match[1]).encode()).hexdigest()
                preload = re.search(r"href=[\"\']([^\"\']*entitylist/getlist[^\"\']*)[\"\']", page)
                if not preload:
                    raise ListError("not_shared_list")
                endpoint = validate_url(urljoin(resolved, html.unescape(preload[1])))
                if urlsplit(endpoint).hostname != "www.google.com":
                    raise ListError("invalid_response")
                _, raw = await self._get(endpoint, browser=True)
                return parse_response(raw, key)
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as err:
            raise ListError("cannot_connect") from err

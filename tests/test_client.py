"""Synthetic fixtures contain no real shared lists or saved locations."""

import json
from unittest.mock import AsyncMock

import pytest

from custom_components.google_maps_lists.client import (
    ListError,
    MapsClient,
    parse_response,
    validate_url,
)


def response(items=None):
    return json.dumps([[None, None, None, None, "Example", None, None, None, items or []]])


def item(name="Example place", lat=51.0, lon=-1.0, key="place-id"):
    return [None, [None, None, "Address", None, None, [None, None, lat, lon], None, key], name]


def test_parse():
    result = parse_response(")]}'\n" + response([item()]), "list-key")
    assert result.name == "Example"
    assert result.places[0].latitude == 51.0
    assert result.places[0].key == "place-id"


def test_dropped_pin_identity_survives_rename():
    a = parse_response(response([item(key=None)]), "x")
    b = parse_response(response([item(name="Renamed", key=None)]), "x")
    assert a.places[0].key == b.places[0].key


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "[]",
        "null",
        "not json",
        response([[None]]),
        response([item(lat=91)]),
        response([item(lat=True)]),
        response([item(lon=float("nan"))]),
        response([item(lat=None)]),
        response([item(), item(lat=52)]),
    ],
)
def test_incomplete_response_is_never_empty_success(raw):
    with pytest.raises(ListError, match="invalid_response"):
        parse_response(raw, "x")


def test_empty_list_and_duplicates():
    assert parse_response(response(), "x").places == ()
    assert len(parse_response(response([item(), item()]), "x").places) == 1


def test_limit():
    with pytest.raises(ListError, match="list_too_large"):
        parse_response(response([item()] * 500), "x")


@pytest.mark.parametrize(
    "url",
    [
        "http://www.google.com/maps",
        "https://evil.test/maps",
        "https://www.google.com.evil.test/maps",
        "https://www.google.com:444/maps",
        "https://user:password@www.google.com/maps",
        "https://127.0.0.1/maps",
        "https://www.google.com/search?q=test",
    ],
)
def test_disallow_non_maps_requests(url):
    with pytest.raises(ListError, match="invalid_url"):
        validate_url(url)


async def test_fetch_shared_list(monkeypatch):
    client = MapsClient(None)
    fetch = AsyncMock(
        side_effect=[
            (
                "https://www.google.com/maps/placelists/list/example",
                '<link href="/maps/preview/entitylist/getlist?x=1&amp;y=2">',
            ),
            ("https://www.google.com/maps/preview/entitylist/getlist", response([item()])),
        ]
    )
    monkeypatch.setattr(client, "_get", fetch)
    snapshot = await client.fetch("https://maps.app.goo.gl/example")
    assert len(snapshot.places) == 1
    assert fetch.call_args.args[0].endswith("?x=1&y=2")


async def test_private_page_rejected(monkeypatch):
    client = MapsClient(None)
    monkeypatch.setattr(
        client,
        "_get",
        AsyncMock(return_value=("https://www.google.com/maps/placelists/list/example", "Sign in")),
    )
    with pytest.raises(ListError, match="not_shared_list"):
        await client.fetch("https://maps.app.goo.gl/example")


async def test_mobile_shared_url_format(monkeypatch):
    client = MapsClient(None)
    monkeypatch.setattr(
        client,
        "_get",
        AsyncMock(
            side_effect=[
                (
                    "https://www.google.com/maps/@/data=!4m3!11m2!2sexample!3e3",
                    '<link href="/maps/preview/entitylist/getlist?pb=x">',
                ),
                ("https://www.google.com/maps/preview/entitylist/getlist", response([item()])),
            ]
        ),
    )
    result = await client.fetch("https://maps.app.goo.gl/example")
    assert result.places[0].key == "place-id"


async def test_redirect_to_untrusted_host_is_not_requested():
    from unittest.mock import MagicMock

    session = MagicMock()
    response = AsyncMock()
    response.status = 302
    response.headers = {"Location": "http://127.0.0.1/internal"}
    session.get.return_value.__aenter__.return_value = response
    with pytest.raises(ListError, match="invalid_url"):
        await MapsClient(session)._get("https://maps.app.goo.gl/example")
    assert session.get.call_count == 1

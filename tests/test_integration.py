"""Exercise setup, real zones, labels, polling and UI with Home Assistant."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.google_maps_lists.client import ListError, Place, SavedList
from custom_components.google_maps_lists.const import DEFAULTS, DOMAIN

SNAPSHOT = SavedList(
    "example-key", "Example list", (Place("place-a", "Example place", 51.0, -1.0),)
)


async def setup(hass, *, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=SNAPSHOT.name,
        data={"url": "https://maps.app.goo.gl/example"},
        unique_id=SNAPSHOT.key,
        options=DEFAULTS | (options or {}),
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.google_maps_lists.client.MapsClient.fetch",
        AsyncMock(return_value=SNAPSHOT),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_zone_lifecycle_and_labels(hass):
    entry = await setup(hass)
    registry = er.async_get(hass)
    zone_id = registry.async_get_entity_id("zone", DOMAIN, f"{entry.entry_id}:place-a")
    state = hass.states.get(zone_id)
    assert state.attributes["latitude"] == 51.0
    assert state.attributes["radius"] == 100
    assert state.attributes["source_list"] == SNAPSHOT.name
    assert state.attributes["editable"] is False
    label = lr.async_get(hass).async_get_label_by_name("Google Maps")
    assert label.label_id in registry.async_get(zone_id).labels
    await entry.runtime_data.zones.sync(
        SavedList(SNAPSHOT.key, "Renamed list", (Place("place-a", "Renamed place", 52, -2),))
    )
    assert hass.states.get(zone_id).attributes["latitude"] == 52
    assert hass.states.get(zone_id).attributes["source_list"] == "Renamed list"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(zone_id) is None or hass.states.get(zone_id).state == "unavailable"


async def test_refresh_failure_preserves_zones(hass):
    entry = await setup(hass)
    zone = next(iter(entry.runtime_data.zones.entities.values()))
    with patch(
        "custom_components.google_maps_lists.client.MapsClient.fetch",
        AsyncMock(side_effect=ListError("invalid_response")),
    ):
        await entry.runtime_data.async_refresh()
    assert not entry.runtime_data.last_update_success
    assert hass.states.get(zone.entity_id).attributes["latitude"] == 51.0
    assert len(entry.runtime_data.zones.entities) == 1


@pytest.mark.parametrize("remove_missing", [False, True])
async def test_remove_only_owned_zones(hass, remove_missing):
    assert await async_setup_component(
        hass,
        "zone",
        {"zone": [{"name": "Existing", "latitude": 51, "longitude": -1, "radius": 100}]},
    )
    entry = await setup(hass, options={"remove_missing": remove_missing})
    zone_id = next(iter(entry.runtime_data.zones.entities.values())).entity_id
    await entry.runtime_data.zones.sync(SavedList(SNAPSHOT.key, SNAPSHOT.name, ()))
    assert bool(entry.runtime_data.zones.entities) != remove_missing
    assert hass.states.get("zone.existing") is not None
    if remove_missing:
        assert er.async_get(hass).async_get(zone_id) is None


async def test_setup_ui(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    with patch(
        "custom_components.google_maps_lists.client.MapsClient.fetch",
        AsyncMock(return_value=SNAPSHOT),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "https://maps.app.goo.gl/example"}
        )
    assert result["step_id"] == "settings"
    with patch(
        "custom_components.google_maps_lists.async_setup_entry", AsyncMock(return_value=True)
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DEFAULTS)
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["result"].options["radius"] == 100


async def test_invalid_url_shows_form_error(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "https://example.test/private"}
    )
    assert result["errors"] == {"base": "invalid_url"}


async def test_restore_offline_and_stable_entity_id(hass):
    entry = await setup(hass)
    zone_id = next(iter(entry.runtime_data.zones.entities.values())).entity_id
    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(
        "custom_components.google_maps_lists.client.MapsClient.fetch",
        AsyncMock(side_effect=ListError("cannot_connect")),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert next(iter(entry.runtime_data.zones.entities.values())).entity_id == zone_id
    assert hass.states.get(zone_id).attributes["latitude"] == 51.0
    assert not entry.runtime_data.last_update_success


async def test_zone_participates_in_presence_detection(hass):
    from homeassistant.components.zone import async_active_zone

    entry = await setup(hass)
    zone = next(iter(entry.runtime_data.zones.entities.values()))
    assert async_active_zone(hass, 51.0, -1.0).entity_id == zone.entity_id


async def test_changed_list_never_replaces_zones(hass):
    entry = await setup(hass, options={"remove_missing": True})
    with patch(
        "custom_components.google_maps_lists.client.MapsClient.fetch",
        AsyncMock(return_value=SavedList("different", "Different list", ())),
    ):
        await entry.runtime_data.async_refresh()
    assert not entry.runtime_data.last_update_success
    assert len(entry.runtime_data.zones.entities) == 1


async def test_options_ui(hass):
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", AsyncMock(return_value=True)
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], DEFAULTS | {"radius": 250, "label": "Favourites"}
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options["radius"] == 250


async def test_duplicate_list_is_rejected(hass):
    await setup(hass)
    with patch(
        "custom_components.google_maps_lists.client.MapsClient.fetch",
        AsyncMock(return_value=SNAPSHOT),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={"url": "https://maps.app.goo.gl/example"},
        )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_refresh_button_recovers_from_failure(hass):
    entry = await setup(hass)
    with patch(
        "custom_components.google_maps_lists.client.MapsClient.fetch",
        AsyncMock(side_effect=ListError("cannot_connect")),
    ):
        await entry.runtime_data.async_refresh()
    button_id = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_refresh"
    )
    assert hass.states.get(button_id).state != "unavailable"
    with patch(
        "custom_components.google_maps_lists.client.MapsClient.fetch",
        AsyncMock(return_value=SNAPSHOT),
    ):
        await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
        await hass.async_block_till_done()
    assert entry.runtime_data.last_update_success

"""Synchronise Google Maps saved lists into Home Assistant zones."""

import logging
from datetime import timedelta

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ListError, MapsClient, SavedList
from .const import DEFAULTS, DOMAIN
from .zones import ZoneManager

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "button"]


class ListCoordinator(DataUpdateCoordinator):
    """Fetch, reconcile, then durably cache the last successful snapshot."""

    def __init__(self, hass, entry):
        options = DEFAULTS | entry.options
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            config_entry=entry,
            update_interval=timedelta(minutes=options["refresh_minutes"]),
        )
        self.entry = entry
        self.client = MapsClient(async_get_clientsession(hass))
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.zones = ZoneManager(hass, entry, options)

    async def _async_update_data(self):
        try:
            snapshot = await self.client.fetch(self.entry.data["url"])
            if snapshot.key != self.entry.unique_id:
                raise ListError("list_changed")
        except ListError as err:
            raise UpdateFailed(str(err)) from None
        await self.zones.sync(snapshot)
        # Keep retained places in the cache too when remove_missing is off.
        cached = SavedList(
            snapshot.key,
            snapshot.name,
            tuple(z.place for z in self.zones.entities.values()),
        ).as_dict()
        await self.store.async_save(cached)
        return snapshot


async def async_setup_entry(hass, entry):
    coordinator = ListCoordinator(hass, entry)
    try:
        if cached := await coordinator.store.async_load():
            try:
                snapshot = SavedList.from_dict(cached)
                if snapshot.key == entry.unique_id:
                    await coordinator.zones.sync(snapshot)
                    coordinator.async_set_updated_data(snapshot)
            except KeyError, TypeError, ValueError:
                _LOGGER.warning("Ignoring an invalid Google Maps Lists cache")
        await coordinator.async_refresh()
        if not coordinator.last_update_success and coordinator.data is None:
            raise ConfigEntryNotReady("Unable to read the shared list")
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.zones.close()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.zones.close()
        return True
    return False


async def async_remove_entry(hass, entry):
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()

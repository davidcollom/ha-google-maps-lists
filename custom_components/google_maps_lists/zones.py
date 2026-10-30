"""Real Zone entities with stable IDs, source attributes and native labels."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.zone import Zone
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.entity_platform import EntityPlatform

from .client import Place, SavedList
from .const import DOMAIN


class ListZone(Zone):
    """Let HA's zone implementation handle people and geofences."""

    def __init__(self, entry_id: str, place: Place, snapshot: SavedList, options: dict):
        self.place = place
        self.source_name = snapshot.name
        self.source_key = snapshot.key
        self.place_key = place.key
        super().__init__(self.config_for(entry_id, place, options))
        self.editable = False
        self._generate_attrs()

    @staticmethod
    def config_for(entry_id: str, place: Place, options: dict) -> dict:
        return {
            "id": f"{entry_id}:{place.key}",
            "name": place.name,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "radius": options["radius"],
            "passive": options["passive"],
            "icon": options["icon"],
        }

    def _generate_attrs(self):
        super()._generate_attrs()
        self._attr_extra_state_attributes.update(
            {
                "source": DOMAIN,
                "source_list": self.source_name,
                "source_list_id": self.source_key,
                "google_place_id": self.place_key,
            }
        )


class ZoneManager:
    """Own a dedicated zone platform; never adopt or edit unrelated zones.

    Zone has no normal config-entry platform forwarding hook. Isolate this
    EntityPlatform adapter here and exercise it against HA in the test suite.
    """

    def __init__(self, hass, entry, options):
        self.hass, self.entry, self.options = hass, entry, options
        self.entities: dict[str, ListZone] = {}
        self.platform = EntityPlatform(
            hass=hass,
            logger=logging.getLogger(__name__),
            domain="zone",
            platform_name=DOMAIN,
            platform=None,
            scan_interval=timedelta(seconds=60),
            entity_namespace=None,
        )
        self.platform.config_entry = entry
        self.platform.async_prepare()

    async def sync(self, snapshot: SavedList) -> None:
        incoming = {p.key: p for p in snapshot.places}
        for key, place in incoming.items():
            if key in self.entities:
                entity = self.entities[key]
                entity.place = place
                entity.source_name = snapshot.name
                config = entity.config_for(self.entry.entry_id, place, self.options)
                if entity.entity_id in self.platform.entities:
                    await entity.async_update_config(config)
                else:
                    # Disabled registry entries have no active state to write.
                    entity._config = config
                    entity._set_attrs_from_config()
                entity._generate_attrs()
                if entity.entity_id in self.platform.entities:
                    entity.async_write_ha_state()
            else:
                entity = ListZone(self.entry.entry_id, place, snapshot, self.options)
                await self.platform.async_add_entities([entity])
                self.entities[key] = entity
        registry = er.async_get(self.hass)
        labels = lr.async_get(self.hass)
        label_name = self.options["label"].strip()
        label = labels.async_get_label_by_name(label_name) if label_name else None
        label_id = label.label_id if label else None
        if label_name and label_id is None:
            label_id = labels.async_create(label_name, icon="mdi:google-maps").label_id
        if label_id:
            for entity in self.entities.values():
                if record := registry.async_get(entity.entity_id):
                    registry.async_update_entity(
                        record.entity_id, labels=record.labels | {label_id}
                    )
        if self.options["remove_missing"]:
            for key in self.entities.keys() - incoming.keys():
                entity = self.entities.pop(key)
                if entity.entity_id in self.platform.entities:
                    await self.platform.async_remove_entity(entity.entity_id)
                if registry.async_get(entity.entity_id):
                    registry.async_remove(entity.entity_id)

    async def close(self) -> None:
        await self.platform.async_destroy()

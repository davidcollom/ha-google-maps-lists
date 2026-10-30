"""Expose list size and refresh availability."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PlaceCount(entry.runtime_data, entry)])


class PlaceCount(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:map-marker-multiple"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_places"
        self._attr_name = f"{entry.title} saved places"

    @property
    def native_value(self):
        return len(self.coordinator.data.places) if self.coordinator.data else None

    @property
    def extra_state_attributes(self):
        return {"managed_zones": len(self.coordinator.zones.entities)}

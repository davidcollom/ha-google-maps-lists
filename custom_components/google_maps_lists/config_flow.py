"""UI setup: one shared list per config entry."""

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ListError, MapsClient, validate_url
from .const import DEFAULTS, DOMAIN

_LOGGER = logging.getLogger(__name__)


def options_schema(options):
    return vol.Schema(
        {
            vol.Required("radius", default=options["radius"]): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=5000, step=10, unit_of_measurement="m", mode="box"
                )
            ),
            vol.Required(
                "refresh_minutes", default=options["refresh_minutes"]
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=15, max=1440, step=1, unit_of_measurement="min", mode="box"
                )
            ),
            vol.Required("label", default=options["label"]): str,
            vol.Required("icon", default=options["icon"]): selector.IconSelector(),
            vol.Required("passive", default=options["passive"]): bool,
            vol.Required("remove_missing", default=options["remove_missing"]): bool,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                url = validate_url(user_input["url"])
                snapshot = await MapsClient(async_get_clientsession(self.hass)).fetch(url)
            except ListError as err:
                errors["base"] = str(err)
            else:
                await self.async_set_unique_id(snapshot.key)
                self._abort_if_unique_id_configured()
                self._list_url = url
                self._list_name = snapshot.name
                return await self.async_step_settings()
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required("url"): str}), errors=errors
        )

    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=self._list_name, data={"url": self._list_url}, options=user_input
            )
        return self.async_show_form(step_id="settings", data_schema=options_schema(DEFAULTS))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["settings", "place"])

    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="settings", data_schema=options_schema(DEFAULTS | self.config_entry.options)
        )

    def _owned_zones(self):
        return {
            record.entity_id: record
            for record in er.async_entries_for_config_entry(
                er.async_get(self.hass), self.config_entry.entry_id
            )
            if record.domain == "zone" and record.platform == DOMAIN
        }

    async def async_step_place(self, user_input=None):
        zones = self._owned_zones()
        if not zones:
            return self.async_abort(reason="no_zones")
        errors = {}
        if user_input is not None:
            if user_input["entity_id"] in zones:
                self._zone_id = user_input["entity_id"]
                return await self.async_step_customize()
            errors["base"] = "zone_missing"
        choices = [
            selector.SelectOptionDict(
                value=record.entity_id,
                label=f"{record.name or record.original_name or record.entity_id} ({record.entity_id})",
            )
            for record in sorted(
                zones.values(), key=lambda z: z.name or z.original_name or z.entity_id
            )
        ]
        return self.async_show_form(
            step_id="place",
            data_schema=vol.Schema(
                {
                    vol.Required("entity_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices, mode="dropdown")
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_customize(self, user_input=None):
        record = self._owned_zones().get(self._zone_id)
        if record is None:
            return self.async_abort(reason="zone_missing")
        labels = lr.async_get(self.hass)
        default_name = (DEFAULTS | self.config_entry.options)["label"].strip()
        default_label = labels.async_get_label_by_name(default_name) if default_name else None
        default_ids = {default_label.label_id} if default_label else set()
        errors = {}
        if user_input is not None:
            group_ids = set(user_input.get("labels", []))
            if any(labels.async_get_label(label_id) is None for label_id in group_ids):
                errors["base"] = "group_missing"
            else:
                er.async_get(self.hass).async_update_entity(
                    record.entity_id,
                    name=user_input.get("name", "").strip() or None,
                    icon=user_input.get("icon", "").strip() or None,
                    labels=group_ids | default_ids,
                )
                return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="customize",
            data_schema=vol.Schema(
                {
                    vol.Optional("name", description={"suggested_value": record.name or ""}): str,
                    vol.Optional(
                        "icon", description={"suggested_value": record.icon or ""}
                    ): selector.IconSelector(
                        selector.IconSelectorConfig(
                            placeholder=record.original_icon or DEFAULTS["icon"]
                        )
                    ),
                    vol.Optional(
                        "labels",
                        description={"suggested_value": sorted(record.labels - default_ids)},
                    ): selector.LabelSelector(selector.LabelSelectorConfig(multiple=True)),
                }
            ),
            description_placeholders={
                "place": record.original_name or record.entity_id,
                "default_group": default_name or "None",
            },
            errors=errors,
        )

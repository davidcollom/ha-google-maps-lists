"""UI setup: one shared list per config entry."""

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
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
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=options_schema(DEFAULTS | self.config_entry.options)
        )

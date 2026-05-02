"""Config flow for The Frame."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TheFrameApi, TheFrameApiError
from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN


class TheFrameConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-entry UI setup. The user only provides the add-on URL."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()

            api = TheFrameApi(async_get_clientsession(self.hass), base_url)
            try:
                await api.status()
            except TheFrameApiError as err:
                errors["base"] = "cannot_connect"
                self.context["error_detail"] = str(err)
            else:
                return self.async_create_entry(
                    title="The Frame", data={CONF_BASE_URL: base_url}
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=(user_input or {}).get(CONF_BASE_URL, DEFAULT_BASE_URL),
                ): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

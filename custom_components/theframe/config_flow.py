"""Config flow for The Frame."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TheFrameApi, TheFrameApiError
from .const import CONF_BASE_URL, CONF_IGNORE_KEYWORDS, DEFAULT_BASE_URL, DOMAIN


class TheFrameConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-entry UI setup. The user only provides the add-on URL."""

    VERSION = 1

    async def _validate_url(self, base_url: str) -> str | None:
        """Probe the add-on. Returns an error key on failure, None on success."""
        api = TheFrameApi(async_get_clientsession(self.hass), base_url)
        try:
            await api.status()
        except TheFrameApiError as err:
            self.context["error_detail"] = str(err)
            return "cannot_connect"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()

            if (err := await self._validate_url(base_url)) is not None:
                errors["base"] = err
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change the add-on URL of an existing entry.

        Allows pointing at a different add-on instance (different host,
        port, or simply a typo fix) without losing entity history. The
        unique_id is updated alongside the URL so the registry stays
        consistent.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")

            # Reject if a *different* entry already uses this URL.
            for other in self._async_current_entries():
                if other.entry_id != entry.entry_id and other.unique_id == base_url:
                    return self.async_abort(reason="already_configured")

            if (err := await self._validate_url(base_url)) is not None:
                errors["base"] = err
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=base_url,
                    data_updates={CONF_BASE_URL: base_url},
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=(user_input or {}).get(
                        CONF_BASE_URL, entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
                    ),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return TheFrameOptionsFlow(entry)


class TheFrameOptionsFlow(OptionsFlow):
    """Lets the user configure which keywords to hide from the integration.

    Useful for marker-tags like `TheFrame` or `exportiert` that the user
    sets in their photo library for housekeeping but that don't make
    sensible category switches / media-browser folders on their own.
    """

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            raw = user_input.get(CONF_IGNORE_KEYWORDS, "") or ""
            ignored = [k.strip() for k in raw.split(",") if k.strip()]
            return self.async_create_entry(
                title="", data={CONF_IGNORE_KEYWORDS: ignored}
            )

        current = self.entry.options.get(CONF_IGNORE_KEYWORDS, [])
        if isinstance(current, list):
            current = ", ".join(current)

        schema = vol.Schema(
            {
                vol.Optional(CONF_IGNORE_KEYWORDS, default=current): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

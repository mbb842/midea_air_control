"""Config flow for the Midea Air Control (AirControlBase) integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SessionManager
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class MideaAirControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Midea Air Control."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step where the user enters credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]

            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            api = SessionManager(
                async_get_clientsession(self.hass),
                email,
                user_input[CONF_PASSWORD],
            )
            result = await api.login_and_save_session()

            if result.get("status") != "success":
                _LOGGER.error("Failed to connect to AirControlBase: %s", result)
                errors["base"] = "cannot_connect"
            elif not api.user_id:
                errors["base"] = "invalid_auth"
            else:
                return self.async_create_entry(title=email, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

"""The Midea Air Control (AirControlBase) integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SessionManager
from .const import DEVICE_IMAGE_FILE, DEVICE_IMAGE_URL, DOMAIN
from .coordinator import MideaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE]

type MideaConfigEntry = ConfigEntry[MideaDataUpdateCoordinator]


async def _async_register_image(hass: HomeAssistant) -> None:
    """Serve the bundled device symbol once, at DEVICE_IMAGE_URL."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("image_registered"):
        return
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                DEVICE_IMAGE_URL,
                str(Path(__file__).parent / DEVICE_IMAGE_FILE),
                False,
            )
        ]
    )
    domain_data["image_registered"] = True


async def async_setup_entry(hass: HomeAssistant, entry: MideaConfigEntry) -> bool:
    """Set up Midea Air Control from a config entry."""
    _LOGGER.info("Setting up Midea Air Control for %s", entry.data[CONF_EMAIL])
    await _async_register_image(hass)

    api = SessionManager(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )

    login = await api.login_and_save_session()
    if login.get("status") != "success" or not api.user_id:
        message = login.get("message", "invalid credentials")
        _LOGGER.error("Unable to log in to AirControlBase: %s", message)
        raise ConfigEntryNotReady(f"Unable to log in to AirControlBase: {message}")

    coordinator = MideaDataUpdateCoordinator(hass, api, entry.entry_id)
    await coordinator.async_load_last_on_state()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info(
        "Midea Air Control set up with %d device(s)", len(coordinator.data)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MideaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: MideaConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)

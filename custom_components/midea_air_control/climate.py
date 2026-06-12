"""Climate platform for the Midea Air Control (AirControlBase) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_OFF,
    SWING_OFF,
    SWING_ON,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MideaConfigEntry
from .const import DEVICE_IMAGE_URL, DOMAIN, MAX_TEMP, MIN_TEMP
from .coordinator import MideaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Map AirControlBase modes to Home Assistant HVAC modes (and back).
MIDEA_TO_HA_HVAC_MODE = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "fan": HVACMode.FAN_ONLY,
    "dry": HVACMode.DRY,
    "auto": HVACMode.AUTO,
}
HA_TO_MIDEA_HVAC_MODE = {v: k for k, v in MIDEA_TO_HA_HVAC_MODE.items()}

# Map AirControlBase fan speeds to Home Assistant fan modes (and back). "off" is
# documented as a valid wind value, used mainly in heat/auto scenarios.
MIDEA_TO_HA_FAN_MODE = {
    "auto": FAN_AUTO,
    "low": FAN_LOW,
    "mid": FAN_MEDIUM,
    "high": FAN_HIGH,
    "off": FAN_OFF,
}
HA_TO_MIDEA_FAN_MODE = {v: k for k, v in MIDEA_TO_HA_FAN_MODE.items()}

# Serialize control commands: the shared session re-logs-in and retries on
# cookie expiry, so overlapping writes could race over the same session.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MideaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Midea Air Control climate entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        MideaClimateEntity(coordinator, device_id)
        for device_id, device in coordinator.data.items()
        if device.get("exist") != "n"
    )


class MideaClimateEntity(
    CoordinatorEntity[MideaDataUpdateCoordinator], ClimateEntity
):
    """Representation of a single AirControlBase air conditioner zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_entity_picture = DEVICE_IMAGE_URL
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_hvac_modes = [HVACMode.OFF, *MIDEA_TO_HA_HVAC_MODE.values()]
    _attr_fan_modes = list(MIDEA_TO_HA_FAN_MODE.values())
    _attr_swing_modes = [SWING_ON, SWING_OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self, coordinator: MideaDataUpdateCoordinator, device_id: int
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"midea_ac_{device_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            name=self._device.get("name"),
        )
        # Track availability so we log only on transitions, not every poll.
        self._was_available = True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data, logging device availability transitions.

        Only acts on a successful poll, so a whole-poll failure (already logged
        as an error by the coordinator) doesn't make every entity log too.
        """
        if self.coordinator.last_update_success:
            available = self.available
            if available != self._was_available:
                if available:
                    _LOGGER.info("%s is back online", self.name)
                else:
                    _LOGGER.warning(
                        "%s is no longer reported by the cloud", self.name
                    )
                self._was_available = available
        super()._handle_coordinator_update()

    @property
    def _device(self) -> dict:
        """Return this entity's current device-state dict from the coordinator.

        Falls back to an empty dict if the zone is missing from the latest poll,
        so state properties degrade gracefully instead of raising.
        """
        return self.coordinator.data.get(self._device_id, {})

    @property
    def available(self) -> bool:
        """Return True if the device is present in the latest poll."""
        return super().available and self._device_id in self.coordinator.data

    @property
    def current_temperature(self) -> float | None:
        """Return the current ambient temperature."""
        return self._device.get("factTemp")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self._device.get("setTemp")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current operating mode."""
        if self._device.get("power") != "y":
            return HVACMode.OFF
        return MIDEA_TO_HA_HVAC_MODE.get(self._device.get("mode"), HVACMode.AUTO)

    @property
    def fan_mode(self) -> str:
        """Return the current fan speed."""
        return MIDEA_TO_HA_FAN_MODE.get(self._device.get("wind"), FAN_AUTO)

    @property
    def swing_mode(self) -> str:
        """Return the current swing mode."""
        return SWING_ON if self._device.get("swing") == "y" else SWING_OFF

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self._send_command({"setTemp": int(temperature)})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set a new operating mode."""
        if hvac_mode == HVACMode.OFF:
            await self._send_command({"power": "n"})
        else:
            await self._send_command(
                self._power_on_changes(mode=HA_TO_MIDEA_HVAC_MODE[hvac_mode])
            )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set a new fan speed."""
        await self._send_command({"wind": HA_TO_MIDEA_FAN_MODE[fan_mode]})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Turn swing on or off."""
        await self._send_command({"swing": "y" if swing_mode == SWING_ON else "n"})

    async def async_turn_on(self) -> None:
        """Turn the air conditioner on."""
        await self._send_command(self._power_on_changes())

    async def async_turn_off(self) -> None:
        """Turn the air conditioner off."""
        await self._send_command({"power": "n"})

    def _power_on_changes(self, **explicit: Any) -> dict:
        """Build a power-on command, restoring the last real settings.

        The cloud forgets mode/setTemp/wind while a unit is off and reports
        defaults (cool / 20C). When turning a unit on from off we resend the
        remembered settings so it resumes where it left off, then apply any
        explicitly requested change (e.g. a chosen mode) on top. Sending them as
        part of the command also pins them through the cloud-lag window.
        """
        changes = {"power": "y"}
        if self.hvac_mode == HVACMode.OFF:
            changes.update(self.coordinator.get_last_on_state(self._device_id))
        changes.update(explicit)
        return changes

    async def _send_command(self, changes: dict) -> None:
        """Send a control command and optimistically update local state."""
        _LOGGER.info("User command for %s: %s", self.name, changes)
        resp = await self.coordinator.api.control_device(self._device, changes)
        if resp.get("code") != "200":
            error = resp.get("msg", resp)
            _LOGGER.error("Failed to send command to %s: %s", self.name, error)
            raise HomeAssistantError(
                f"Failed to send command to {self.name}: {error}"
            )
        # The cloud takes a few seconds to report the new state, so register
        # the change with the coordinator (which keeps it applied across polls
        # until the cloud catches up) and reflect it locally right away.
        self.coordinator.async_set_optimistic(self._device_id, changes)
        self._device.update(changes)
        self.async_write_ha_state()

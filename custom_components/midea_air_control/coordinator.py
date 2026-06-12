"""Data update coordinator for the Midea Air Control integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from time import monotonic

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SessionManager
from .const import (
    API_DELAY_SEC,
    DOMAIN,
    LAST_ON_FIELDS,
    SCAN_INTERVAL_SEC,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Debounce persisting the last-on state so a burst of polls writes once.
STORE_SAVE_DELAY_SEC = 5


class MideaDataUpdateCoordinator(DataUpdateCoordinator[dict[int, dict]]):
    """Fetch all device state from AirControlBase in a single request.

    The ``getDetails`` endpoint returns every zone in one response, so a single
    coordinator feeds all climate entities instead of each one polling on its own.
    ``data`` is a mapping of device id -> device-state dict.
    """

    def __init__(
        self, hass: HomeAssistant, api: SessionManager, entry_id: str
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SEC),
        )
        self.api = api
        # Just-sent commands to keep applied on top of polls until the cloud
        # reflects them. Maps device id -> (changes, monotonic expiry).
        self._optimistic: dict[int, tuple[dict, float]] = {}
        # Last powered-on mode/setTemp/wind per device, used to restore settings
        # the cloud drops while a unit is off. Persisted across restarts.
        self._last_on_state: dict[int, dict] = {}
        self._store: Store[dict[str, dict]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )

    async def async_load_last_on_state(self) -> None:
        """Load the persisted last powered-on state for each device."""
        if stored := await self._store.async_load():
            self._last_on_state = {int(key): value for key, value in stored.items()}

    @callback
    def get_last_on_state(self, device_id: int) -> dict:
        """Return the remembered mode/setTemp/wind for a device (may be empty)."""
        return dict(self._last_on_state.get(device_id, {}))

    def _remember_on_state(self, data: dict[int, dict]) -> None:
        """Capture the settings of every powered-on device for later restore.

        Reads the post-optimistic view, so a just-issued turn-on (which pins the
        restored mode/setTemp/wind) is captured as intended rather than as the
        cloud's transient off-defaults.
        """
        changed = False
        for device_id, device in data.items():
            if device.get("power") != "y":
                continue
            snapshot = {
                field: device[field]
                for field in LAST_ON_FIELDS
                if device.get(field) is not None
            }
            if snapshot and snapshot != self._last_on_state.get(device_id):
                self._last_on_state[device_id] = snapshot
                changed = True
        if changed:
            self._store.async_delay_save(self._data_to_store, STORE_SAVE_DELAY_SEC)

    @callback
    def _data_to_store(self) -> dict[str, dict]:
        """Serialize the last-on state for the store (JSON keys must be strings)."""
        return {str(key): value for key, value in self._last_on_state.items()}

    def _apply_last_on_state(self, data: dict[int, dict]) -> dict[int, dict]:
        """Show the last real settings while a unit is off.

        The cloud reports defaults (cool / 20C) for an off unit, so overlay the
        remembered mode/setTemp/wind for display. Untouched fields like
        ``factTemp`` keep their fresh polled values.
        """
        for device_id, device in data.items():
            if device.get("power") == "y":
                continue
            if remembered := self._last_on_state.get(device_id):
                data[device_id] = {**device, **remembered}
        return data

    @callback
    def async_set_optimistic(self, device_id: int, changes: dict) -> None:
        """Remember a just-sent command so polls don't briefly revert it.

        The cloud takes several seconds to report a change back, so until the
        window expires (or a poll already reflects it) ``changes`` are overlaid
        onto the polled state for this device.

        Each service call only carries its own delta (e.g. just ``setTemp`` or
        just ``mode``), so merge into any still-pending changes rather than
        replacing them; otherwise a quick second command would drop the first
        (e.g. setting temperature then mode would lose the optimistic setTemp).
        """
        pending = self._optimistic.get(device_id, ({}, 0.0))[0]
        self._optimistic[device_id] = (
            {**pending, **changes},
            monotonic() + API_DELAY_SEC,
        )

    def _apply_optimistic(self, data: dict[int, dict]) -> dict[int, dict]:
        """Overlay pending optimistic changes onto freshly polled data."""
        now = monotonic()
        for device_id, (changes, expiry) in list(self._optimistic.items()):
            device = data.get(device_id)
            if device is None:
                # Device dropped from the poll; availability logging covers it.
                del self._optimistic[device_id]
                continue
            if all(device.get(key) == value for key, value in changes.items()):
                _LOGGER.debug(
                    "Device %s confirmed command %s", device_id, changes
                )
                del self._optimistic[device_id]
                continue
            if now >= expiry:
                # API returned success but the device never reflected the change.
                _LOGGER.warning(
                    "Device %s did not reflect command %s within %ds; "
                    "reverting to polled state",
                    device_id,
                    changes,
                    API_DELAY_SEC,
                )
                del self._optimistic[device_id]
                continue
            data[device_id] = {**device, **changes}
        return data

    async def _async_update_data(self) -> dict[int, dict]:
        """Fetch the latest state for all devices."""
        _LOGGER.debug("Polling AirControlBase for device state")
        result = await self.api.get_devices()
        if result.get("status") != "success":
            raise UpdateFailed(f"Error fetching devices: {result.get('message')}")
        data = {int(device["id"]): device for device in result["devices"]}
        data = self._apply_optimistic(data)
        self._remember_on_state(data)
        data = self._apply_last_on_state(data)
        _LOGGER.debug("Polled %d device(s): %s", len(data), data)
        return data

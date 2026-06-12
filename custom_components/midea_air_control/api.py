"""HTTP client for the AirControlBase cloud API."""

import asyncio
import json
import logging

import aiohttp
import async_timeout
from yarl import URL

_LOGGER = logging.getLogger(__name__)

# Application-level code (returned with HTTP 200) meaning the session cookie is
# invalid or expired: {"code": 40018, "msg": "用户没登陆"} ("user not logged in").
SESSION_EXPIRED_CODE = "40018"


class SessionManager:
    """Manage authentication against the AirControlBase API.

    Uses Home Assistant's shared aiohttp ``ClientSession`` and stores the
    ``JSESSIONID`` cookie and ``userId`` returned by login, which are required
    for all subsequent requests.
    """

    def __init__(
        self, session: aiohttp.ClientSession, account: str, password: str
    ) -> None:
        """Initialize the session manager with HA's shared aiohttp session."""
        self.url_base = "https://www.aircontrolbase.com/web"
        self.session = session
        self.jsession_id = None
        self.account = account
        self.password = password
        self.user_id = None
        # Serializes re-authentication so concurrent expired requests trigger a
        # single login. ``_login_generation`` increments on each successful
        # login, letting callers detect whether someone else already re-logged
        # in while they were waiting for the lock.
        self._auth_lock = asyncio.Lock()
        self._login_generation = 0

    def _get_session_cookie(self):
        """Return the JSESSIONID currently held in the session's cookie jar."""
        cookies = self.session.cookie_jar.filter_cookies(URL(self.url_base))
        if "JSESSIONID" in cookies:
            return cookies["JSESSIONID"].value
        return None

    async def login_and_save_session(self):
        """Make async POST request and save JSESSIONID cookie."""
        # Form data
        data = {
            "account": self.account,
            "password": self.password,
            "from": "web"
        }

        # Headers for form submission
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        try:
            async with async_timeout.timeout(10):
                url = self.url_base + "/user/login"
                async with self.session.post(url, data=data, headers=headers) as response:
                    if response.status == 200:
                        result_text = await response.text()

                        # The shared aiohttp session's cookie jar automatically
                        # stores and replays JSESSIONID for us. Read it back from
                        # the jar (robust against redirects and reused sessions)
                        # purely so we know a session is established.
                        self.jsession_id = self._get_session_cookie()
                        _LOGGER.debug("JSESSIONID present: %s", bool(self.jsession_id))

                        # Try to parse the user ID from the login response
                        try:
                            result_json = json.loads(result_text)
                            if result_json.get("code") == "200" and "result" in result_json:
                                self.user_id = result_json["result"].get("id")
                                _LOGGER.debug("User ID saved: %s", self.user_id)
                                _LOGGER.info(
                                    "Authenticated with AirControlBase as %s",
                                    self.account,
                                )
                        except json.JSONDecodeError:
                            pass

                        # Mark a new authenticated session for concurrency control.
                        self._login_generation += 1

                        return {
                            "status": "success",
                            "jsession_id": self.jsession_id,
                            "user_id": self.user_id,
                            "response": result_text
                        }
                    error_text = await response.text()
                    _LOGGER.error(
                        "Login failed (HTTP %s): %s", response.status, error_text
                    )
                    return {
                        "status": "error",
                        "code": response.status,
                        "message": error_text
                    }
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Login request failed: %s", e)
            return {
                "status": "error",
                "message": f"Request failed: {e}"
            }

    async def _reauthenticate(self, seen_generation: int) -> bool:
        """Re-login at most once across concurrent callers.

        ``seen_generation`` is the login generation the caller observed before
        its request failed. While waiting for the lock, another caller may have
        already re-authenticated; if the generation advanced, we skip the
        redundant login and report success. Returns True if a valid session is
        now available.
        """
        async with self._auth_lock:
            if self._login_generation != seen_generation:
                # Someone else already re-logged in while we waited.
                _LOGGER.debug("Session already refreshed by another request")
                return True
            _LOGGER.info("Session expired, renewing AirControlBase credentials")
            login_result = await self.login_and_save_session()
            return login_result.get("status") == "success"

    async def make_authenticated_request(self, url: str, method: str = "GET", data: dict = None, is_retry: bool = False):
        """Make request using saved JSESSIONID. Re-authenticates if cookie expired."""
        if not self.session:
            return {"status": "error", "message": "Session not initialized"}

        if not self.jsession_id and not is_retry:
            _LOGGER.debug("No JSESSIONID found. Attempting to log in first.")
            login_result = await self.login_and_save_session()
            if login_result.get("status") != "success":
                return {"status": "error", "message": "Login failed prior to request."}

        # Snapshot the login generation so _reauthenticate can tell whether a
        # concurrent caller already refreshed the session.
        seen_generation = self._login_generation

        # Do NOT pass cookies manually: the shared aiohttp session's cookie jar
        # already stores the JSESSIONID from login and replays it on every
        # request. Passing cookies= here would override the jar's valid cookie.
        headers = {}
        if method.upper() == "POST" and data:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            async with async_timeout.timeout(10):
                if method.upper() == "GET":
                    async with self.session.get(url) as response:
                        result_text = await response.text()
                elif method.upper() == "POST":
                    async with self.session.post(url, data=data, headers=headers) as response:
                        result_text = await response.text()

            # Try to parse the response as JSON
            try:
                result = json.loads(result_text)
            except json.JSONDecodeError:
                # If it's not JSON (e.g. it returned an HTML login page due to redirect)
                result = {"code": "not_json", "raw": result_text}

            # Only an expired/invalid session warrants a re-login + retry. Other
            # application errors (e.g. a 400 bad payload) are returned as-is so
            # we don't waste a login round-trip on non-auth failures.
            if str(result.get("code")) == SESSION_EXPIRED_CODE and not is_retry:
                if await self._reauthenticate(seen_generation):
                    return await self.make_authenticated_request(url, method, data, is_retry=True)
                _LOGGER.error("Re-authentication failed")
                return {"status": "error", "message": "Authentication retry failed."}

            return result

        except Exception as e:  # noqa: BLE001
            _LOGGER.error("API Request failed: %s", e)
            return {"status": "error", "message": str(e)}

    async def get_devices(self):
        """Fetch all devices linked to this account."""
        if not self.user_id:
            _LOGGER.error("Cannot fetch devices: no user ID (login may have failed)")
            return {"status": "error", "message": "No user ID available. Login may have failed."}

        url = self.url_base + "/userGroup/getDetails"
        data = {"userId": self.user_id}

        response = await self.make_authenticated_request(url, method="POST", data=data)

        devices = []
        try:
            if response.get("code") == "200":
                result = response.get("result", {})
                areas = result.get("areas", [])
                for area in areas:
                    area_data = area.get("data", [])
                    for device in area_data:
                        devices.append(device)
                return {"status": "success", "devices": devices}
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Failed to parse devices: %s", e)

        return {"status": "error", "message": "Failed to parse devices", "raw": response}

    async def control_device(self, device: dict, changes: dict):
        """Send a command to a device.

        ``device`` is the current full device-state dict (from getDetails) and
        ``changes`` is the delta to apply (e.g. ``{"power": "y", "mode": "cool"}``).

        The API expects two URL-encoded JSON strings: ``control`` (the full
        intended target state, including the device ``id``) and ``operation``
        (the key target variables). We build both from the current state merged
        with the requested changes, as recommended by the API documentation.
        """
        url = self.url_base + "/device/control"
        target = {**device, **changes}

        control = {
            "power": target.get("power", "y"),
            "mode": target.get("mode", "auto"),
            "setTemp": target.get("setTemp"),
            "wind": target.get("wind", "auto"),
            "swing": target.get("swing", "n"),
            "lock": target.get("lock", ""),
            "factTemp": target.get("factTemp"),
            "modeLockValue": target.get("modeLockValue", ""),
            "coolLockValue": target.get("coolLockValue", ""),
            "heatLockValue": target.get("heatLockValue", ""),
            "windLockValue": target.get("windLockValue", ""),
            "unlock": target.get("unlock", "mode,cool,heat,wind,remote"),
            "id": device.get("id"),
        }
        operation = {
            "power": target.get("power", "y"),
            "mode": target.get("mode", "auto"),
            "setTemp": target.get("setTemp"),
            "wind": target.get("wind", "auto"),
        }
        data = {
            "userId": self.user_id,
            "control": json.dumps(control, separators=(",", ":")),
            "operation": json.dumps(operation, separators=(",", ":")),
            "type": "control",
        }

        _LOGGER.debug("Sending control for device %s: %s", device.get("id"), operation)
        return await self.make_authenticated_request(url, method="POST", data=data)

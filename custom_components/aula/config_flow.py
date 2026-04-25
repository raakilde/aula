"""Aula config flow — OAuth2 token-based setup.

The user obtains access + refresh tokens via the sniffer tool
(or from the Aula mobile app via mitmproxy) and pastes them here.
Tokens are refreshed automatically via the standard OAuth2 refresh_token grant.
"""

import logging
import time
from typing import Any, Dict, Optional

import requests
import voluptuous as vol
from homeassistant import config_entries

from .const import (
    API,
    API_VERSION,
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOLSCHEDULE,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UGEPLAN,
    DOMAIN,
    OAUTH_CLIENT_ID,
    OAUTH_TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_TOKEN): str,
        vol.Required(CONF_REFRESH_TOKEN): str,
        vol.Required(CONF_DEVICE_ID): str,
        vol.Optional(CONF_SCHOOLSCHEDULE, default=True): bool,
        vol.Optional(CONF_UGEPLAN, default=True): bool,
    }
)


class AulaCustomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Aula config flow — paste OAuth2 tokens from sniffer."""

    VERSION = 4

    def __init__(self) -> None:
        """Initialize config flow state."""
        self._reauth_entry: Optional[config_entries.ConfigEntry] = None

    async def async_step_user(self, user_input=None):
        """Step 1: paste access + refresh token + device_id.

        After token validation, the integration will execute the post-auth
        initialization sequence matching the iOS app:
        1. profiles.getProfileContext (widgets)
        2. profiles.getprofilesbylogin (children/profiles)
        3. notifications.registerDevice
        4. configuration methods
        5. notifications.getNotificationsForActiveProfile
        6. posts.getAllPosts
        """
        errors = {}

        if user_input is not None:
            access_token = user_input[CONF_ACCESS_TOKEN].strip()
            refresh_token = user_input[CONF_REFRESH_TOKEN].strip()
            device_id = user_input[CONF_DEVICE_ID].strip()

            _LOGGER.info(
                "Aula setup: Validating tokens with device_id=%s",
                device_id[:20] + "..." if len(device_id) > 20 else device_id,
            )

            # Quick validation: try the access token against the Aula API
            valid = await self.hass.async_add_executor_job(
                self._test_token, access_token, device_id
            )

            if valid:
                _LOGGER.info(
                    "Aula setup: Token validated, will initialize with post-auth flow"
                )
                return self.async_create_entry(
                    title="Aula",
                    data={
                        CONF_ACCESS_TOKEN: access_token,
                        CONF_REFRESH_TOKEN: refresh_token,
                        CONF_DEVICE_ID: device_id,
                        CONF_TOKEN_EXPIRES_AT: time.time() + 3600,
                        CONF_SCHOOLSCHEDULE: user_input.get(CONF_SCHOOLSCHEDULE, True),
                        CONF_UGEPLAN: user_input.get(CONF_UGEPLAN, True),
                    },
                )
            else:
                # Token rejected — try refreshing instead
                _LOGGER.debug("Aula setup: Access token invalid, attempting refresh")
                new_tokens = await self.hass.async_add_executor_job(
                    self._try_refresh, refresh_token, device_id
                )
                if new_tokens:
                    _LOGGER.info(
                        "Aula setup: Tokens refreshed successfully, "
                        "will initialize with post-auth flow"
                    )
                    return self.async_create_entry(
                        title="Aula",
                        data={
                            CONF_ACCESS_TOKEN: new_tokens["access_token"],
                            CONF_REFRESH_TOKEN: new_tokens.get(
                                "refresh_token", refresh_token
                            ),
                            CONF_DEVICE_ID: device_id,
                            CONF_TOKEN_EXPIRES_AT: time.time()
                            + new_tokens.get("expires_in", 3600),
                            CONF_SCHOOLSCHEDULE: user_input.get(
                                CONF_SCHOOLSCHEDULE, True
                            ),
                            CONF_UGEPLAN: user_input.get(CONF_UGEPLAN, True),
                        },
                    )
                _LOGGER.warning("Aula setup: Token validation and refresh failed")
                errors["base"] = "invalid_token"

        return self.async_show_form(
            step_id="user",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Handle Home Assistant initiated re-authentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )

        if self._reauth_entry is None:
            return self.async_abort(reason="unknown")

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Confirm re-authentication by pasting fresh tokens.

        After token validation, the integration will execute the post-auth
        initialization sequence matching the iOS app:
        1. profiles.getProfileContext (widgets)
        2. profiles.getprofilesbylogin (children/profiles)
        3. notifications.registerDevice
        4. configuration methods
        5. notifications.getNotificationsForActiveProfile
        6. posts.getAllPosts
        """
        errors = {}

        if user_input is not None:
            access_token = user_input[CONF_ACCESS_TOKEN].strip()
            refresh_token = user_input[CONF_REFRESH_TOKEN].strip()
            device_id = user_input[CONF_DEVICE_ID].strip()

            _LOGGER.info(
                "Aula reauth: Validating tokens with device_id=%s",
                device_id[:20] + "..." if len(device_id) > 20 else device_id,
            )

            valid = await self.hass.async_add_executor_job(
                self._test_token, access_token, device_id
            )

            if not valid:
                _LOGGER.debug("Aula reauth: Access token invalid, attempting refresh")
                new_tokens = await self.hass.async_add_executor_job(
                    self._try_refresh, refresh_token, device_id
                )
                if new_tokens:
                    access_token = new_tokens["access_token"]
                    refresh_token = new_tokens.get("refresh_token", refresh_token)
                    _LOGGER.info(
                        "Aula reauth: Tokens refreshed successfully, "
                        "initialization will proceed with post-auth flow"
                    )
                else:
                    _LOGGER.warning("Aula reauth: Token refresh failed")
                    errors["base"] = "invalid_token"

            if not errors:
                reauth_entry = self._reauth_entry
                if reauth_entry is None:
                    return self.async_abort(reason="unknown")

                new_data = dict(reauth_entry.data)
                new_data[CONF_ACCESS_TOKEN] = access_token
                new_data[CONF_REFRESH_TOKEN] = refresh_token
                new_data[CONF_DEVICE_ID] = device_id
                new_data[CONF_TOKEN_EXPIRES_AT] = time.time() + 3600

                self.hass.config_entries.async_update_entry(reauth_entry, data=new_data)
                _LOGGER.info("Aula reauth: Reloading integration with new tokens")
                await self.hass.config_entries.async_reload(reauth_entry.entry_id)

                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_TOKEN): str,
                    vol.Required(CONF_REFRESH_TOKEN): str,
                    vol.Required(CONF_DEVICE_ID): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return options flow handler."""
        return OptionsFlowHandler(config_entry)

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _test_token(access_token: str, device_id: str | None = None) -> bool:
        """Test an access token against the Aula API.

        Tests against multiple API versions to match startup behavior.
        Returns True if at least one version responds with 200 OK.
        """
        versions = ["23", str(API_VERSION), "24", "25", "21"]
        versions = list(dict.fromkeys(versions))

        try:
            for version in versions:
                # getProfileContext is the first post-auth call and works on v23
                # getProfilesByLogin returns 410 (deprecated) or 403 (missing portalRoles[] param)
                url = f"{API}{version}/?method=profiles.getProfileContext&portalrole=guardian"
                if device_id:
                    url += f"&deviceId={device_id}"
                if access_token:
                    url += f"&access_token={access_token}"

                r = requests.get(
                    url,
                    headers={"Accept": "application/json"},
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status", {}).get("message") == "OK":
                        _LOGGER.debug("Aula: Token validated against API v%s", version)
                        return True
                else:
                    _LOGGER.debug(
                        "Aula: Token test against API v%s failed: HTTP %s",
                        version,
                        r.status_code,
                    )
        except Exception as e:
            _LOGGER.debug("Aula: Token test failed: %s", e)
        return False

    @staticmethod
    def _try_refresh(
        refresh_token: str, device_id: str | None = None
    ) -> Optional[dict]:
        """Try to get a new access token via refresh_token grant.

        Note: Refresh tokens are single-use. If already exchanged by the iOS app,
        will fail with "invalid_grant" error with "Cannot decrypt refresh token".
        """
        try:
            # Validate token format
            refresh_token = str(refresh_token).strip()
            if not refresh_token or len(refresh_token) < 20:
                _LOGGER.warning(
                    "Token refresh failed: refresh_token invalid (len=%d)",
                    len(refresh_token),
                )
                return None

            grant_data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            }

            # Include device_id if provided
            if device_id:
                grant_data["device_id"] = device_id

            r = requests.post(
                OAUTH_TOKEN_URL,
                data=grant_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                _LOGGER.debug(
                    "Token refresh succeeded: response keys=%s, expires_in=%s",
                    list(data.keys()),
                    data.get("expires_in", "?"),
                )
                return data
            _LOGGER.warning("Refresh failed: HTTP %s — %s", r.status_code, r.text[:200])
        except Exception as e:
            _LOGGER.warning("Refresh error: %s", e)
        return None


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Aula options — re-paste tokens when expired."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        super().__init__()
        self._config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle options - re-authenticate."""
        return await self.async_step_reauth(user_input)

    async def async_step_reauth(self, user_input=None):
        """Re-authenticate by pasting new tokens."""
        errors = {}

        if user_input is not None:
            access_token = user_input[CONF_ACCESS_TOKEN].strip()
            refresh_token = user_input[CONF_REFRESH_TOKEN].strip()
            device_id = user_input[CONF_DEVICE_ID].strip()

            valid = await self.hass.async_add_executor_job(
                AulaCustomConfigFlow._test_token, access_token, device_id
            )
            if not valid:
                new_tokens = await self.hass.async_add_executor_job(
                    AulaCustomConfigFlow._try_refresh, refresh_token
                )
                if new_tokens:
                    access_token = new_tokens["access_token"]
                    refresh_token = new_tokens.get("refresh_token", refresh_token)
                else:
                    errors["base"] = "invalid_token"

            if not errors:
                new_data = dict(self._config_entry.data)
                new_data[CONF_ACCESS_TOKEN] = access_token
                new_data[CONF_REFRESH_TOKEN] = refresh_token
                new_data[CONF_DEVICE_ID] = device_id
                new_data[CONF_TOKEN_EXPIRES_AT] = time.time() + 3600
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_TOKEN): str,
                    vol.Required(CONF_REFRESH_TOKEN): str,
                    vol.Required(CONF_DEVICE_ID): str,
                }
            ),
            errors=errors,
        )

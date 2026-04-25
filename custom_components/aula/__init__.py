"""
Based on https://github.com/JBoye/HA-Aula
"""

import asyncio
import logging
import os
import time
from typing import Any

import requests
from homeassistant import config_entries, core
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store

from .const import (
    API,
    API_VERSION,
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    DOMAIN,
    OAUTH_CLIENT_ID,
    OAUTH_TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

_INVALID_DEVICE_ID_VALUES = {"tablet", "phone", "mobile", "unknown"}


def _test_token(access_token: str, device_id: str | None = None) -> bool:
    """Test an access token against the Aula API.

    Uses profiles.getProfileContext which requires portalrole=guardian.
    profiles.getProfilesByLogin returns 410/403 without portalRoles[] param.
    Returns True if at least one version responds with 200 OK.
    """
    versions = ["23", str(API_VERSION), "24", "25", "21"]
    versions = list(dict.fromkeys(versions))

    try:
        for version in versions:
            # getProfileContext is the first call in the post-auth sequence
            # and requires portalrole=guardian (from sniffer capture)
            url = (
                f"{API}{version}/?method=profiles.getProfileContext&portalrole=guardian"
            )
            if device_id:
                url += f"&deviceId={device_id}"
            if access_token:
                url += f"&access_token={access_token}"

            _LOGGER.debug(
                "Testing token against API v%s (device_id=%s, token_len=%d)",
                version,
                device_id[:20] + "..."
                if device_id and len(device_id) > 20
                else device_id,
                len(access_token) if access_token else 0,
            )

            try:
                response = requests.get(
                    url,
                    headers={"Accept": "application/json"},
                    timeout=15,
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status", {}).get("message") == "OK":
                        _LOGGER.debug("Token validated against API v%s", version)
                        return True
                else:
                    _LOGGER.debug(
                        "Token test against API v%s failed: HTTP %s",
                        version,
                        response.status_code,
                    )
            except Exception as exc:
                _LOGGER.debug("Token test against API v%s exception: %s", version, exc)
    except Exception as exc:
        _LOGGER.debug("Token test failed during setup: %s", exc)

    _LOGGER.debug("Token test failed: No API version returned 200 OK")
    return False


def _try_refresh(refresh_token: str, device_id: str | None = None) -> dict | None:
    """Try to get a new access token via refresh_token grant.

    Refresh tokens are single-use. If already exchanged by the iOS app,
    the server will reject with "invalid_grant" error.
    """
    try:
        # Validate token format - detect whitespace/encoding corruption
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

        # Include device_id if provided (might be required by OAuth)
        if device_id:
            grant_data["device_id"] = device_id

        response = requests.post(
            OAUTH_TOKEN_URL,
            data=grant_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if response.status_code == 200:
            data = response.json()
            _LOGGER.debug(
                "Token refresh succeeded: response keys=%s, expires_in=%s",
                list(data.keys()),
                data.get("expires_in", "?"),
            )
            return data

        # Log error response for debugging
        try:
            error_info = response.json()
            _LOGGER.warning(
                "Token refresh failed during setup: HTTP %s — %s",
                response.status_code,
                error_info,
            )
        except Exception:
            _LOGGER.warning(
                "Token refresh failed during setup: HTTP %s", response.status_code
            )
    except Exception as exc:
        _LOGGER.warning("Token refresh error during setup: %s", exc)
    return None


def _ensure_valid_tokens(entry_data: dict) -> dict | None:
    """Ensure entry tokens are valid before forwarding platforms.

    Strategy:
    1) Validate device_id (required for all API calls)
    2) Prefer refresh_token grant at startup to get a fresh access token.
    3) Fall back to validating existing access token if refresh fails.
    4) Validate against multiple API versions (v22→v25, v21 fallback)
    """
    access_token = str(entry_data.get(CONF_ACCESS_TOKEN, "")).strip()
    refresh_token = str(entry_data.get(CONF_REFRESH_TOKEN, "")).strip()
    device_id = str(entry_data.get(CONF_DEVICE_ID, "")).strip()

    _LOGGER.debug(
        "Aula startup: Token validation — device_id=%s, has_access=%s, has_refresh=%s",
        device_id[:20] + "..." if len(device_id) > 20 else device_id,
        bool(access_token),
        bool(refresh_token),
    )

    # Device ID is required for all API calls (matches post-auth flow)
    if not device_id or device_id.lower() in _INVALID_DEVICE_ID_VALUES:
        raise ConfigEntryAuthFailed(
            "Aula session expired. Missing device id. Reconfigure the Aula integration with fresh tokens and device id from sniffer."
        )

    if not refresh_token and not access_token:
        raise ConfigEntryAuthFailed(
            "Aula session expired. Reconfigure the Aula integration with fresh tokens."
        )

    # STRATEGY 1: Try refresh_token first (always prefer fresh tokens)
    if refresh_token:
        _LOGGER.debug("Aula startup: Attempting token refresh...")
        refreshed = _try_refresh(refresh_token, device_id)
        if refreshed:
            # Log what the refresh response contains
            _LOGGER.debug(
                "Aula startup: Refresh response keys: %s, expires_in=%s",
                list(refreshed.keys()),
                refreshed.get("expires_in", "?"),
            )
            new_access_token = refreshed.get("access_token")
            if not new_access_token:
                _LOGGER.warning(
                    "Aula startup: Refresh succeeded but no access_token in response. "
                    "Response: %s",
                    refreshed,
                )
            else:
                # Trust the OAuth server: if it issued new tokens, they are valid.
                # Don't revalidate via API probe - that adds latency and can fail
                # for unrelated reasons (deprecated endpoints, missing params).
                _LOGGER.info(
                    "Aula startup: Tokens refreshed successfully via OAuth grant (expires in %ss)",
                    refreshed.get("expires_in", 3600),
                )
                return {
                    CONF_ACCESS_TOKEN: new_access_token,
                    CONF_REFRESH_TOKEN: refreshed.get("refresh_token", refresh_token),
                    CONF_TOKEN_EXPIRES_AT: time.time()
                    + refreshed.get("expires_in", 3600),
                }
        else:
            _LOGGER.debug(
                "Aula startup: Token refresh returned None/False. "
                "Trying existing access_token as fallback."
            )

    # STRATEGY 2: Fallback to existing access_token validation
    if access_token:
        _LOGGER.debug("Aula startup: Testing existing access_token...")
        if _test_token(access_token, device_id):
            _LOGGER.debug("Aula startup: Using existing access token (still valid)")
            return None
        else:
            _LOGGER.warning(
                "Aula startup: Existing access_token validation failed. "
                "No valid tokens available."
            )

    # STRATEGY 3: All strategies failed
    _LOGGER.error(
        "Aula startup: All token validation strategies failed. "
        "Refresh failed, existing token invalid. User must reauth."
    )
    raise ConfigEntryAuthFailed(
        "Aula session expired. Reconfigure the Aula integration with fresh tokens."
    )


async def async_setup_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up platform from a ConfigEntry."""
    _LOGGER.info("Setting up Aula integration")

    token_store = Store[dict[str, Any]](hass, 1, "aula_tokens")
    stored_tokens = await token_store.async_load() or {}

    # Backward compatible recovery for older entries where tokens/device id
    # were stored outside config entry data.
    effective_data = dict(entry.data)
    if not effective_data.get(CONF_ACCESS_TOKEN) and stored_tokens.get(
        CONF_ACCESS_TOKEN
    ):
        effective_data[CONF_ACCESS_TOKEN] = stored_tokens.get(CONF_ACCESS_TOKEN)
    if not effective_data.get(CONF_REFRESH_TOKEN) and stored_tokens.get(
        CONF_REFRESH_TOKEN
    ):
        effective_data[CONF_REFRESH_TOKEN] = stored_tokens.get(CONF_REFRESH_TOKEN)
    if not effective_data.get(CONF_DEVICE_ID) and stored_tokens.get(CONF_DEVICE_ID):
        effective_data[CONF_DEVICE_ID] = stored_tokens.get(CONF_DEVICE_ID)
    if not effective_data.get(CONF_TOKEN_EXPIRES_AT):
        effective_data[CONF_TOKEN_EXPIRES_AT] = stored_tokens.get(
            CONF_TOKEN_EXPIRES_AT, stored_tokens.get("expires_at")
        )

    try:
        updated_tokens = await hass.async_add_executor_job(
            _ensure_valid_tokens, effective_data
        )
    except ConfigEntryAuthFailed:
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=effective_data,
        )
        raise

    if updated_tokens or effective_data != dict(entry.data):
        new_data = dict(effective_data)
        new_data.update(updated_tokens)
        hass.config_entries.async_update_entry(entry, data=new_data)

    hass.data.setdefault(DOMAIN, {})
    # Only store non-sensitive config keys in hass.data — never tokens
    _SAFE_KEYS = {"schoolschedule", "ugeplan"}
    hass.data[DOMAIN][entry.entry_id] = {
        k: v for k, v in entry.data.items() if k in _SAFE_KEYS
    }

    # Register options update listener
    unsub_options_update_listener = entry.add_update_listener(options_update_listener)
    hass.data[DOMAIN][entry.entry_id]["unsub_options_update_listener"] = (
        unsub_options_update_listener
    )

    # Set up sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    _LOGGER.info("Aula integration setup complete")
    return True


async def options_update_listener(
    hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry
):
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[hass.config_entries.async_forward_entry_unload(entry, "sensor")]
        )
    )

    # Clean up cached data files containing personal information
    config_dir = hass.config.path()
    for filename in ("skoleskema.json", "uddannelseopgaveliste.json"):
        filepath = os.path.join(config_dir, filename)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                _LOGGER.debug("Removed cached data file: %s", filename)
        except OSError as e:
            _LOGGER.warning("Failed to remove cached data file %s: %s", filename, e)

    # Remove options_update_listener if it exists.
    if "unsub_options_update_listener" in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["unsub_options_update_listener"]()

    # Remove config entry from domain.
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

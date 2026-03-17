"""
Based on https://github.com/JBoye/HA-Aula
"""

import asyncio
import logging
import os

from homeassistant import config_entries, core

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up platform from a ConfigEntry."""
    _LOGGER.info("Setting up Aula integration")

    hass.data.setdefault(DOMAIN, {})
    # Only store non-sensitive config keys in hass.data — never passwords, tokens, or usernames
    _SAFE_KEYS = {"schoolschedule", "ugeplan", "auth_method", "mitid_identity"}
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

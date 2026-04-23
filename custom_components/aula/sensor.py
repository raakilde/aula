"""
Based on https://github.com/JBoye/HA-Aula
"""

import logging
from datetime import timedelta
from typing import Any

from homeassistant import config_entries, core
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import Client
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOLSCHEDULE,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UGEPLAN,
    DOMAIN,
)
from .sensors import (
    AulaAttendanceSensor,
    AulaClosedDaysSensor,
    AulaEducationSensor,
    AulaLibrarySensor,
    AulaMailSensor,
    AulaPostsSensor,
    AulaReminderSensor,
    AulaWeeklyPresenceNextSensor,
    AulaWeeklyPresenceSensor,
    AulaWeeklyScheduleNextSensor,
    AulaWeeklyScheduleSensor,
    AulaWeekNotesSensor,
    AulaWeekPlanSensor,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    """Setup sensors from a config entry created in the integrations UI."""
    config = hass.data[DOMAIN][config_entry.entry_id]

    if config_entry.options:
        config.update(config_entry.options)

    # Set up global feature flag
    global ugeplan

    ugeplan = config.get(CONF_UGEPLAN, True)

    # Persist tokens/deviceId in Home Assistant storage.
    token_store = Store[dict[str, Any]](hass, 1, "aula_tokens")

    async def persist_tokens_callback(updated_tokens):
        """Persist updated tokens/deviceId to Home Assistant storage."""
        try:
            await token_store.async_save(updated_tokens)
            _LOGGER.debug("Successfully persisted updated tokens to storage")
        except Exception as exc:
            _LOGGER.error(f"Failed to persist updated tokens to storage: {exc}")

    # Load tokens/deviceId from Home Assistant storage
    stored_tokens = await token_store.async_load() or {}

    # Always prefer tokens from entry.data — __init__.py just validated/refreshed
    # them so they are fresher than anything in the store. This prevents the client
    # from starting with stale tokens when a refresh happened during setup.
    entry_access = config_entry.data.get(CONF_ACCESS_TOKEN)
    entry_refresh = config_entry.data.get(CONF_REFRESH_TOKEN)
    if entry_access and entry_refresh:
        stored_tokens[CONF_ACCESS_TOKEN] = entry_access
        stored_tokens[CONF_REFRESH_TOKEN] = entry_refresh
        if config_entry.data.get(CONF_TOKEN_EXPIRES_AT):
            stored_tokens[CONF_TOKEN_EXPIRES_AT] = config_entry.data[
                CONF_TOKEN_EXPIRES_AT
            ]
            stored_tokens["expires_at"] = config_entry.data[CONF_TOKEN_EXPIRES_AT]
        stored_tokens[CONF_DEVICE_ID] = config_entry.data.get(
            CONF_DEVICE_ID, stored_tokens.get(CONF_DEVICE_ID)
        )
        await token_store.async_save(stored_tokens)
        _LOGGER.debug("Synced fresh tokens from config entry to token store")

    device_id = stored_tokens.get("device_id")

    client = Client(
        config.get(CONF_SCHOOLSCHEDULE, True),
        config.get(CONF_UGEPLAN, True),
        stored_tokens=stored_tokens,
        token_persist_callback=persist_tokens_callback,
        hass=hass,
        config_entry=config_entry,
        device_id=device_id,
    )

    hass.data[DOMAIN]["client"] = client

    async def async_update_data():
        """Fetch data following post-auth initialization sequence.

        Mirrors the iOS app post-auth flow:
        1. profiles.getProfileContext (widgets)
        2. profiles.getprofilesbylogin (children/profiles)
        3. notifications.registerDevice
        4. configuration methods
        5. notifications.getNotificationsForActiveProfile
        6. posts.getAllPosts
        """
        client = hass.data[DOMAIN]["client"]
        _LOGGER.debug("Aula: Starting post-auth initialization sequence")
        try:
            await hass.async_add_executor_job(client.update_data)
            _LOGGER.debug(
                "Aula: Post-auth flow complete - children=%d, widgets=%d",
                len(getattr(client, "_children", [])),
                len(getattr(client, "widgets", {})),
            )
        except Exception as err:
            _LOGGER.error("Aula: Post-auth initialization failed: %s", err)
            raise

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="sensor",
        update_method=async_update_data,
        update_interval=timedelta(minutes=30),
        config_entry=config_entry,
    )

    # Immediate refresh (non-fatal in platform setup)
    await coordinator.async_request_refresh()

    entities = []
    client = hass.data[DOMAIN]["client"]

    # Get available widgets (populated during coordinator refresh)
    available_widgets = getattr(client, "widgets", {})
    _LOGGER.debug(
        "Aula: Available widgets detected: %s",
        list(available_widgets.keys()) if available_widgets else "none",
    )

    if not getattr(client, "_children", []):
        _LOGGER.warning(
            "Aula: No children data available after post-auth initialization. "
            "This may indicate: (1) authentication failed, (2) device_id mismatch, "
            "(3) network timeout, or (4) API version issue. "
            "Check logs for API version and refresh token status. "
            "Entities will be created on next successful refresh."
        )
        async_add_entities([], update_before_add=True)
        return

    for i, child in enumerate(client._children):
        child_id = str(child["id"])
        # child_name = client._childnames[child["id"]].split()[0]  # unused
        institution_type = getattr(client, "_institution_types", {}).get(
            child["id"], "unknown"
        )

        # Library sensor - independent of daily presence (books exist regardless)
        # Create for school-type children; skip for kindergartens
        if institution_type != "kindergarten":
            _LOGGER.debug(
                f"Creating library sensor for child {child_id} (institution: {institution_type}, "
                f"widget 0019 in available_widgets: {'0019' in available_widgets})"
            )
            entities.append(AulaLibrarySensor(hass, coordinator, child))
        else:
            _LOGGER.debug(f"Library sensor for child {child_id} skipped - kindergarten")

        if client.presence[child_id] == 1:
            if child_id in client._daily_overview:
                _LOGGER.debug(
                    f"Found presence data for child {child_id} at {institution_type} - adding sensor entities."
                )

                # Main attendance/presence sensor - always available
                entities.append(AulaAttendanceSensor(hass, coordinator, child))

                # Education course sensors - check widget availability and institution appropriateness
                if (
                    "0028" in available_widgets
                    and client.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    )
                ):
                    entities.append(AulaEducationSensor(hass, coordinator, child))
                elif (
                    "0028" not in available_widgets
                    or not client.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    )
                ):
                    reason = (
                        "widget not available"
                        if "0028" not in available_widgets
                        else f"not appropriate for {institution_type}"
                    )
                    _LOGGER.info(
                        f"Education sensor for child {child_id} skipped - {reason}"
                    )

                # Week notes sensors - check widget availability and institution appropriateness
                if (
                    "0028" in available_widgets
                    and client.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    )
                ):
                    entities.append(AulaWeekNotesSensor(hass, coordinator, child))
                elif (
                    "0028" not in available_widgets
                    or not client.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    )
                ):
                    reason = (
                        "widget not available"
                        if "0028" not in available_widgets
                        else f"not appropriate for {institution_type}"
                    )
                    _LOGGER.info(
                        f"Week notes sensor for child {child_id} skipped - {reason}"
                    )

                # Week plan sensors - always available for all institution types
                if ugeplan:
                    entities.append(AulaWeekPlanSensor(hass, coordinator, child))
                    # Reminder sensor - check widget availability and institution appropriateness
                    if (
                        "0062" in available_widgets
                        and client.is_widget_appropriate_for_institution(
                            "0062", institution_type
                        )
                    ):
                        entities.append(AulaReminderSensor(hass, coordinator, child))
                    else:
                        reason = (
                            "widget not available"
                            if "0062" not in available_widgets
                            else f"not appropriate for {institution_type}"
                        )
                        _LOGGER.info(
                            f"Reminder sensor for child {child_id} skipped - {reason}"
                        )

                # Weekly Schedule sensor (presence templates) - available for all institution types
                entities.append(AulaWeeklyScheduleSensor(hass, coordinator, child))
                entities.append(AulaWeeklyScheduleNextSensor(hass, coordinator, child))

                # Weekly Presence sensors (komme og gå) - available for all institution types
                entities.append(AulaWeeklyPresenceSensor(hass, coordinator, child))
                entities.append(AulaWeeklyPresenceNextSensor(hass, coordinator, child))

                # Posts sensor - available for all institution types
                entities.append(AulaPostsSensor(hass, coordinator, child))

                # Mail sensor - available for all institution types
                entities.append(AulaMailSensor(hass, coordinator, child))
        else:
            entities.append(AulaAttendanceSensor(hass, coordinator, child))

    # Add institution-wide sensors (only once, not per child)
    if client._children:  # Only add if we have children
        entities.append(AulaClosedDaysSensor(hass, coordinator))

    # We have data and can now set up the calendar platform:
    if config[CONF_SCHOOLSCHEDULE]:
        await hass.config_entries.async_forward_entry_setups(config_entry, ["calendar"])

    # Set up binary sensor platform
    await hass.config_entries.async_forward_entry_setups(
        config_entry, ["binary_sensor"]
    )

    async_add_entities(entities, update_before_add=True)

"""
Based on https://github.com/JBoye/HA-Aula
"""

import logging
from datetime import timedelta

from homeassistant import config_entries, core
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import Client
from .const import (
    CONF_AUTH_COOKIES,
    CONF_SCHOOLSCHEDULE,
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

    # Create callback for persisting updated cookies
    def persist_cookies_callback(updated_cookies):
        """Callback to persist updated cookies to config entry"""
        try:
            _LOGGER.info("Persisting auto-synced cookies to config entry")
            new_data = dict(config_entry.data)
            new_data[CONF_AUTH_COOKIES] = updated_cookies

            hass.config_entries.async_update_entry(
                config_entry,
                data=new_data,
            )
            _LOGGER.debug("Successfully persisted auto-synced cookies")
        except Exception as e:
            _LOGGER.error(f"Failed to persist updated cookies: {e}")

    client = Client(
        config[CONF_SCHOOLSCHEDULE],
        config[CONF_UGEPLAN],
        config.get(CONF_AUTH_COOKIES, {}),
        cookie_persist_callback=persist_cookies_callback,
    )

    hass.data[DOMAIN]["client"] = client

    async def async_update_data():
        client = hass.data[DOMAIN]["client"]
        await hass.async_add_executor_job(client.update_data)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="sensor",
        update_method=async_update_data,
        update_interval=timedelta(minutes=30),
    )

    # Immediate refresh
    await coordinator.async_request_refresh()

    entities = []
    client = hass.data[DOMAIN]["client"]

    # Get available widgets (populated during coordinator refresh)
    available_widgets = getattr(client, "widgets", {})
    _LOGGER.debug(
        f"Available widgets for institution: {list(available_widgets.keys())}"
    )

    if not getattr(client, "_children", []):
        _LOGGER.warning(
            "No children data available - authentication may have failed. "
            "Entities will be created on next successful refresh."
        )
        async_add_entities([], update_before_add=True)
        return

    for i, child in enumerate(client._children):
        child_id = str(child["id"])
        child_name = client._childnames[child["id"]].split()[0]
        institution_type = getattr(client, "_institution_types", {}).get(
            child["id"], "unknown"
        )

        # Library sensor - independent of daily presence (books exist regardless)
        # Create for school-type children; skip for kindergartens
        if institution_type != "kindergarten":
            _LOGGER.debug(
                f"Creating library sensor for {child_name} (institution: {institution_type}, "
                f"widget 0019 in available_widgets: {'0019' in available_widgets})"
            )
            entities.append(AulaLibrarySensor(hass, coordinator, child))
        else:
            _LOGGER.debug(f"Library sensor for {child_name} skipped - kindergarten")

        if client.presence[child_id] == 1:
            if child_id in client._daily_overview:
                _LOGGER.debug(
                    f"Found presence data for {child_name} (childid {child_id}) at {institution_type} - adding sensor entities."
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
                        f"Education sensor for {child_name} skipped - {reason}"
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
                        f"Week notes sensor for {child_name} skipped - {reason}"
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
                            f"Reminder sensor for {child_name} skipped - {reason}"
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

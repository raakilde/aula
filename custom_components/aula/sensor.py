"""
Based on https://github.com/JBoye/HA-Aula
"""

import logging
from datetime import datetime, timedelta

from homeassistant import config_entries, core
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import Client
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

from .const import (
    CONF_AUTH_COOKIES,
    CONF_BIBLIOTEK,
    CONF_MINUDANNELSEFORLOEB,
    CONF_MINUDANNELSEOPGAVELISTE,
    CONF_MINUDANNELSEUGENOTE,
    CONF_SCHOOLSCHEDULE,
    CONF_UGEPLAN,
)

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

    # Set up global feature flags
    global \
        ugeplan, \
        bibliotek, \
        minuddannelseforloeb, \
        minuddannelseopgaveliste, \
        minuddannelseugenote

    ugeplan = config.get(CONF_UGEPLAN, True)
    bibliotek = config.get(CONF_BIBLIOTEK, True)
    minuddannelseforloeb = config.get(CONF_MINUDANNELSEFORLOEB, True)
    minuddannelseopgaveliste = config.get(CONF_MINUDANNELSEOPGAVELISTE, True)
    minuddannelseugenote = config.get(CONF_MINUDANNELSEUGENOTE, True)

    # from .client import Client

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
        config[CONF_BIBLIOTEK],
        config[CONF_MINUDANNELSEFORLOEB],
        config[CONF_MINUDANNELSEOPGAVELISTE],
        config[CONF_MINUDANNELSEUGENOTE],
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
    await hass.async_add_executor_job(client.update_data)

    for i, child in enumerate(client._children):
        if client.presence[str(child["id"])] == 1:
            if str(child["id"]) in client._daily_overview:
                _LOGGER.debug(
                    "Found presence data for childid "
                    + str(child["id"])
                    + " adding sensor entities."
                )

                # Main attendance/presence sensor
                entities.append(AulaAttendanceSensor(hass, coordinator, child))

                # Library sensor
                if bibliotek and "0019" in client.widgets:
                    entities.append(AulaLibrarySensor(hass, coordinator, child))

                # Education course sensors
                if minuddannelseforloeb and "0028" in client.widgets:
                    entities.append(AulaEducationSensor(hass, coordinator, child))

                # Week notes sensors
                if minuddannelseugenote and "0028" in client.widgets:
                    entities.append(AulaWeekNotesSensor(hass, coordinator, child))

                # Week plan sensors
                if ugeplan:
                    entities.append(AulaWeekPlanSensor(hass, coordinator, child))
                    if "0062" in client.widgets:
                        entities.append(AulaReminderSensor(hass, coordinator, child))
        else:
            entities.append(AulaAttendanceSensor(hass, coordinator, child))
    # We have data and can now set up the calendar platform:
    if config[CONF_SCHOOLSCHEDULE]:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setups(config_entry, ["calendar"])
        )
    ####
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setups(config_entry, ["binary_sensor"])
    )
    ####
    #
    async_add_entities(entities, update_before_add=True)


class AulaAttendanceSensor(Entity):
    """Main attendance/presence sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        institution = self._client._institutions[self._child["id"]]
        return f"{institution} {childname} Attendance"

    @property
    def state(self):
        """
        0 = IKKE KOMMET
        1 = SYG
        2 = FERIE/FRI
        3 = KOMMET/TIL STEDE
        4 = PÅ TUR
        5 = SOVER
        8 = HENTET/GÅET
        """
        if self._client.presence[str(self._child["id"])] == 1:
            states = [
                "Ikke kommet",
                "Syg",
                "Ferie/Fri",
                "Kommet/Til stede",
                "På tur",
                "Sover",
                "6",
                "7",
                "Gået",
            ]
            daily_info = self._client._daily_overview[str(self._child["id"])]
            return states[daily_info["status"]]
        else:
            return "n/a"

    @property
    def extra_state_attributes(self):
        attributes = {}

        if self._client.presence[str(self._child["id"])] == 1:
            daily_info = self._client._daily_overview[str(self._child["id"])]

            try:
                attributes["profilePicture"] = daily_info["institutionProfile"][
                    "profilePicture"
                ]["url"]
            except:
                attributes["profilePicture"] = None

            # Daily attendance fields
            fields = [
                "location",
                "sleepIntervals",
                "checkInTime",
                "checkOutTime",
                "activityType",
                "entryTime",
                "exitTime",
                "exitWith",
                "comment",
                "spareTimeActivity",
                "selfDeciderStartTime",
                "selfDeciderEndTime",
            ]

            for attribute in fields:
                if attribute == "exitTime" and daily_info[attribute] == "23:59:00":
                    attributes[attribute] = None
                else:
                    try:
                        attributes[attribute] = datetime.strptime(
                            daily_info[attribute], "%H:%M:%S"
                        ).strftime("%H:%M")
                    except:
                        attributes[attribute] = daily_info[attribute]

        return attributes

    @property
    def unique_id(self):
        return f"aula_attendance_{self._child['id']}"

    @property
    def icon(self):
        return "mdi:account-school"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class AulaLibrarySensor(Entity):
    """Library books sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        institution = self._client._institutions[self._child["id"]]
        return f"{institution} {childname} Library"

    @property
    def state(self):
        try:
            books = self._client.loaned_books[self._child["name"]]
            if isinstance(books, list):
                return len(books)
            return 0
        except:
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}
        try:
            attributes["books"] = self._client.loaned_books[self._child["name"]]
        except:
            attributes["books"] = "Not available"
        return attributes

    @property
    def unique_id(self):
        return f"aula_library_{self._child['id']}"

    @property
    def icon(self):
        return "mdi:book-multiple"

    @property
    def unit_of_measurement(self):
        return "books"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class AulaEducationSensor(Entity):
    """Education courses sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        institution = self._client._institutions[self._child["id"]]
        return f"{institution} {childname} Education"

    @property
    def state(self):
        try:
            forloeb = self._client.forloebthisweek[self._child["name"]]
            if forloeb and forloeb != "Not available":
                return "active"
            return "none"
        except:
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}
        try:
            attributes["forloebThisWeek"] = self._client.forloebthisweek[
                self._child["name"]
            ]
        except:
            attributes["forloebThisWeek"] = "Not available"

        try:
            attributes["forloebNextWeek"] = self._client.forloebthisweek[
                self._child["name"]
            ]
        except:
            attributes["forloebNextWeek"] = "Not available"

        return attributes

    @property
    def unique_id(self):
        return f"aula_education_{self._child['id']}"

    @property
    def icon(self):
        return "mdi:school"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class AulaWeekNotesSensor(Entity):
    """Weekly notes sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        institution = self._client._institutions[self._child["id"]]
        return f"{institution} {childname} Week Notes"

    @property
    def state(self):
        try:
            note_this = self._client.ugenotethisweek[self._child["name"]]
            note_next = self._client.ugenotenextweek[self._child["name"]]
            has_notes = (note_this and note_this != "Not available") or (
                note_next and note_next != "Not available"
            )
            return "available" if has_notes else "none"
        except:
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}
        try:
            attributes["ugenotethisweek"] = self._client.ugenotethisweek[
                self._child["name"]
            ]
        except:
            attributes["ugenotethisweek"] = "Not available"

        try:
            attributes["ugenotenextweek"] = self._client.ugenotenextweek[
                self._child["name"]
            ]
        except:
            attributes["ugenotenextweek"] = "Not available"

        return attributes

    @property
    def unique_id(self):
        return f"aula_weeknotes_{self._child['id']}"

    @property
    def icon(self):
        return "mdi:notebook"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class AulaWeekPlanSensor(Entity):
    """Week plan sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        institution = self._client._institutions[self._child["id"]]
        return f"{institution} {childname} Week Plan"

    @property
    def state(self):
        try:
            plan_this = self._client.ugep_attr[self._child["name"].split()[0]]
            plan_next = self._client.ugepnext_attr[self._child["name"].split()[0]]
            has_plans = (plan_this and plan_this != "Not available") or (
                plan_next and plan_next != "Not available"
            )
            return "available" if has_plans else "none"
        except:
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}
        try:
            attributes["ugeplan"] = self._client.ugep_attr[
                self._child["name"].split()[0]
            ]
        except:
            attributes["ugeplan"] = "Not available"

        try:
            attributes["ugeplan_next"] = self._client.ugepnext_attr[
                self._child["name"].split()[0]
            ]
        except:
            attributes["ugeplan_next"] = "Not available"

        return attributes

    @property
    def unique_id(self):
        return f"aula_weekplan_{self._child['id']}"

    @property
    def icon(self):
        return "mdi:calendar-week"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class AulaReminderSensor(Entity):
    """Reminder list sensor for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        institution = self._client._institutions[self._child["id"]]
        return f"{institution} {childname} Reminders"

    @property
    def state(self):
        try:
            reminders = self._client.huskeliste[self._child["name"].split()[0]]
            if isinstance(reminders, list):
                return len(reminders)
            return 0
        except:
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}
        try:
            attributes["huskelisten"] = self._client.huskeliste[
                self._child["name"].split()[0]
            ]
        except:
            attributes["huskelisten"] = "Not available"
        return attributes

    @property
    def unique_id(self):
        return f"aula_reminders_{self._child['id']}"

    @property
    def icon(self):
        return "mdi:clipboard-list"

    @property
    def unit_of_measurement(self):
        return "items"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

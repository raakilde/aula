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

                # Weekly Schedule sensor (presence templates)
                entities.append(AulaWeeklyScheduleSensor(hass, coordinator, child))
                entities.append(AulaWeeklyScheduleNextSensor(hass, coordinator, child))
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
        return f"Attendance - {childname}"

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
                "Not arrived",
                "Sick",
                "Vacation/Free",
                "Present",
                "On trip",
                "Sleeping",
                "6",
                "7",
                "Left",
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
                attributes["profile_picture"] = daily_info["institutionProfile"][
                    "profilePicture"
                ]["url"]
            except:
                attributes["profile_picture"] = None

            # Daily attendance fields
            field_mapping = {
                "location": "location",
                "sleepIntervals": "sleep_intervals",
                "checkInTime": "check_in_time",
                "checkOutTime": "check_out_time",
                "activityType": "activity_type",
                "entryTime": "entry_time",
                "exitTime": "exit_time",
                "exitWith": "exit_with",
                "comment": "comment",
                "spareTimeActivity": "spare_time_activity",
                "selfDeciderStartTime": "self_decider_start_time",
                "selfDeciderEndTime": "self_decider_end_time",
            }

            for field, attr_name in field_mapping.items():
                if field == "exitTime" and daily_info[field] == "23:59:00":
                    attributes[attr_name] = None
                else:
                    try:
                        attributes[attr_name] = datetime.strptime(
                            daily_info[field], "%H:%M:%S"
                        ).strftime("%H:%M")
                    except:
                        attributes[attr_name] = daily_info[field]

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"attendance_{childname}"

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
        return f"Library - {childname}"

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
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"library_{childname}"

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
        return f"Education - {childname}"

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
            attributes["course_this_week"] = self._client.forloebthisweek[
                self._child["name"]
            ]
        except:
            attributes["course_this_week"] = "Not available"

        try:
            attributes["course_next_week"] = self._client.forloebthisweek[
                self._child["name"]
            ]
        except:
            attributes["course_next_week"] = "Not available"

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"education_{childname}"

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
        return f"Week Notes - {childname}"

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
            attributes["week_note_this_week"] = self._client.ugenotethisweek[
                self._child["name"]
            ]
        except:
            attributes["week_note_this_week"] = "Not available"

        try:
            attributes["week_note_next_week"] = self._client.ugenotenextweek[
                self._child["name"]
            ]
        except:
            attributes["week_note_next_week"] = "Not available"

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"week_notes_{childname}"

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
        return f"Week Plan - {childname}"

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
            attributes["week_plan_this_week"] = self._client.ugep_attr[
                self._child["name"].split()[0]
            ]
        except:
            attributes["week_plan_this_week"] = "Not available"

        try:
            attributes["week_plan_next_week"] = self._client.ugepnext_attr[
                self._child["name"].split()[0]
            ]
        except:
            attributes["week_plan_next_week"] = "Not available"

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"week_plan_{childname}"

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
        return f"Reminders - {childname}"

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
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"reminders_{childname}"

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


class AulaWeeklyScheduleSensor(Entity):
    """Weekly schedule sensor showing presence templates for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Weekly Schedule - {childname}"

    @property
    def state(self):
        try:
            childname = self._client._childnames[self._child["id"]].split()[0]
            schedule = self._client.presence_templates.get(childname, {})
            if schedule and schedule.get("day_templates"):
                # Count number of scheduled days
                scheduled_days = sum(
                    1 for day in schedule["day_templates"] if day.get("entryTime")
                )
                return f"{scheduled_days} days scheduled"
            return "No schedule"
        except Exception:
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}
        try:
            childname = self._client._childnames[self._child["id"]].split()[0]
            schedule = self._client.presence_templates.get(childname, {})

            if schedule:
                attributes["institution"] = schedule.get("institution", "")
                attributes["child_name"] = schedule.get("child_name", "")

                # Format day templates into more readable format
                weekly_schedule = []
                day_names = [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ]

                for day_template in schedule.get("day_templates", []):
                    day_info = {
                        "day": day_names[day_template.get("dayOfWeek", 1) - 1],
                        "date": day_template.get("byDate", ""),
                        "entry_time": day_template.get("entryTime", ""),
                        "exit_time": day_template.get("exitTime", ""),
                        "exit_with": day_template.get("exitWith", ""),
                        "is_on_vacation": day_template.get("isOnVacation", False),
                        "comment": day_template.get("comment", ""),
                        "activity_type": day_template.get("activityType"),
                    }

                    # Add vacation info if applicable
                    if day_template.get("vacation"):
                        day_info["vacation_title"] = day_template["vacation"].get(
                            "title", ""
                        )
                        day_info["vacation_description"] = (
                            day_template["vacation"]
                            .get("description", {})
                            .get("html", "")
                        )

                    weekly_schedule.append(day_info)

                attributes["weekly_schedule"] = weekly_schedule

        except Exception as e:
            attributes["error"] = str(e)

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"weekly_schedule_{childname}"

    @property
    def icon(self):
        return "mdi:calendar-clock"

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



class AulaWeeklyScheduleNextSensor(Entity):
    """Weekly schedule sensor showing next week's presence templates for a child."""

    def __init__(self, hass, coordinator, child) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Weekly Schedule Next Week - {childname}"

    @property
    def state(self):
        try:
            childname = self._client._childnames[self._child["id"]].split()[0]
            schedule = self._client.presence_templates_next.get(childname, {})
            if schedule and schedule.get("day_templates"):
                # Count number of scheduled days
                scheduled_days = sum(1 for day in schedule["day_templates"] if day.get("entryTime"))
                return f"{scheduled_days} days scheduled"
            return "No schedule"
        except Exception as e:
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}
        try:
            childname = self._client._childnames[self._child["id"]].split()[0]
            schedule = self._client.presence_templates_next.get(childname, {})
            
            if schedule:
                attributes["institution"] = schedule.get("institution", "")
                attributes["child_name"] = schedule.get("child_name", "")
                
                # Format day templates into more readable format
                weekly_schedule = []
                day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                
                for day_template in schedule.get("day_templates", []):
                    day_info = {
                        "day": day_names[day_template.get("dayOfWeek", 1) - 1],
                        "date": day_template.get("byDate", ""),
                        "entry_time": day_template.get("entryTime", ""),
                        "exit_time": day_template.get("exitTime", ""),
                        "exit_with": day_template.get("exitWith", ""),
                        "is_on_vacation": day_template.get("isOnVacation", False),
                        "comment": day_template.get("comment", ""),
                        "activity_type": day_template.get("activityType")
                    }
                    
                    # Add vacation info if applicable
                    if day_template.get("vacation"):
                        day_info["vacation_title"] = day_template["vacation"].get("title", "")
                        day_info["vacation_description"] = day_template["vacation"].get("description", {}).get("html", "")
                    
                    weekly_schedule.append(day_info)
                
                attributes["weekly_schedule_next"] = weekly_schedule
                
        except Exception as e:
            attributes["error"] = str(e)
            
        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"weekly_schedule_next_{childname}"

    @property
    def icon(self):
        return "mdi:calendar-arrow-right"

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

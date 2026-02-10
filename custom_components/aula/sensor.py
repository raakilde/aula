"""
Based on https://github.com/JBoye/HA-Aula
"""

import logging
from datetime import datetime, timedelta

from dateutil.parser import parse
from homeassistant import config_entries, core
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import Client
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

from .const import (
    CONF_AUTH_COOKIES,
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
    # Widget-based features are always enabled and controlled by widget availability
    bibliotek = True
    minuddannelseforloeb = True
    minuddannelseopgaveliste = True
    minuddannelseugenote = True

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
        if bibliotek and institution_type != "kindergarten":
            _LOGGER.debug(
                f"Creating library sensor for {child_name} (institution: {institution_type}, "
                f"widget 0019 in available_widgets: {'0019' in available_widgets})"
            )
            entities.append(AulaLibrarySensor(hass, coordinator, child))
        elif bibliotek and institution_type == "kindergarten":
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
                    minuddannelseforloeb
                    and "0028" in available_widgets
                    and client.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    )
                ):
                    entities.append(AulaEducationSensor(hass, coordinator, child))
                elif minuddannelseforloeb and (
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
                    minuddannelseugenote
                    and "0028" in available_widgets
                    and client.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    )
                ):
                    entities.append(AulaWeekNotesSensor(hass, coordinator, child))
                elif minuddannelseugenote and (
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

        # Dynamic names based on institution type
        child_id = self._child["id"]
        institution_type = getattr(self._client, "_institution_types", {}).get(
            child_id, "unknown"
        )

        if institution_type == "kindergarten":
            return f"Attendance - {childname}"
        elif institution_type == "school":
            return f"Attendance - {childname}"
        else:
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
        # Dynamic icons based on institution type
        child_id = self._child["id"]
        institution_type = getattr(self._client, "_institution_types", {}).get(
            child_id, "unknown"
        )

        if institution_type == "kindergarten":
            return "mdi:account-child"
        elif institution_type == "school":
            return "mdi:account-school"
        else:
            return "mdi:account"

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

    def _find_books_for_child(self):
        """Find loaned books for this child, handling name mismatches between Aula and CICERO.

        The CICERO API keys books by patronDisplayName which may differ from the
        Aula child name (e.g. 'Aksel' vs 'Aksel Surname'). This method tries:
        1. Exact match on child["name"]
        2. Match on the childnames lookup (full name from profile)
        3. Partial match: patronDisplayName contains or is contained in child name
        """
        loaned = self._client.loaned_books
        if not loaned:
            return None

        child_name = self._child.get("name", "")
        # Also get the name from _childnames which may be formatted differently
        profile_name = self._client._childnames.get(self._child["id"], "")

        # 1. Exact match on child["name"]
        if child_name in loaned:
            return loaned[child_name]

        # 2. Exact match on profile name
        if profile_name and profile_name in loaned:
            return loaned[profile_name]

        # 3. Partial/first-name matching
        child_first = child_name.split()[0].lower() if child_name else ""
        profile_first = profile_name.split()[0].lower() if profile_name else ""

        for patron_name, books in loaned.items():
            patron_lower = patron_name.lower()
            patron_first = patron_lower.split()[0] if patron_lower else ""

            # First name match
            if child_first and (
                patron_first == child_first or patron_lower == child_first
            ):
                return books
            if profile_first and (
                patron_first == profile_first or patron_lower == profile_first
            ):
                return books

            # Substring match (patron in child name or vice versa)
            if child_name and (
                patron_lower in child_name.lower() or child_name.lower() in patron_lower
            ):
                return books

        return None

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Library - {childname}"

    @property
    def state(self):
        books = self._find_books_for_child()
        if books is not None and isinstance(books, list):
            return len(books)
        return 0

    @property
    def extra_state_attributes(self):
        attributes = {}
        books = self._find_books_for_child()
        if books is not None:
            attributes["books"] = books
        else:
            attributes["books"] = []
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
        # Dynamic icons based on institution type
        child_id = self._child["id"]
        institution_type = getattr(self._client, "_institution_types", {}).get(
            child_id, "unknown"
        )

        if institution_type == "kindergarten":
            return "mdi:human-child"
        elif institution_type == "school":
            return "mdi:school"
        else:
            return "mdi:book-education"

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
            # ugenotethisweek and ugenotenextweek are text strings, not dictionaries
            note_this = getattr(self._client, "ugenotethisweek", None)
            note_next = getattr(self._client, "ugenotenextweek", None)

            # Check if we have valid note content
            has_notes = (
                note_this and isinstance(note_this, str) and note_this.strip()
            ) or (note_next and isinstance(note_next, str) and note_next.strip())

            return "available" if has_notes else "none"
        except Exception as e:
            _LOGGER.debug(f"Week notes sensor state error for {self._child['id']}: {e}")
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            # ugenotethisweek is a text string, not a dictionary
            note_this = getattr(self._client, "ugenotethisweek", None)
            if note_this and isinstance(note_this, str):
                attributes["week_note_this_week"] = note_this
            else:
                attributes["week_note_this_week"] = "Not available"
        except Exception:
            attributes["week_note_this_week"] = "Not available"

        try:
            # ugenotenextweek is a text string, not a dictionary
            note_next = getattr(self._client, "ugenotenextweek", None)
            if note_next and isinstance(note_next, str):
                attributes["week_note_next_week"] = note_next
            else:
                attributes["week_note_next_week"] = "Not available"
        except Exception:
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
            # Get the child's first name to use as key
            child_first_name = self._client._childnames[self._child["id"]].split()[0]

            # Check if we have plan data for this child
            plan_this = self._client.ugep_attr.get(child_first_name)
            plan_next = self._client.ugepnext_attr.get(child_first_name)

            has_plans = (plan_this and plan_this != "Not available") or (
                plan_next and plan_next != "Not available"
            )
            return "available" if has_plans else "none"
        except Exception as e:
            _LOGGER.debug(f"Week plan sensor state error for {self._child['id']}: {e}")
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            # Get the child's first name to use as key
            child_first_name = self._client._childnames[self._child["id"]].split()[0]

            # Get week plan for this week
            plan_this = self._client.ugep_attr.get(child_first_name)
            attributes["week_plan_this_week"] = (
                plan_this if plan_this else "Not available"
            )
        except Exception:
            attributes["week_plan_this_week"] = "Not available"

        try:
            # Get the child's first name to use as key
            child_first_name = self._client._childnames[self._child["id"]].split()[0]

            # Get week plan for next week
            plan_next = self._client.ugepnext_attr.get(child_first_name)
            attributes["week_plan_next_week"] = (
                plan_next if plan_next else "Not available"
            )
        except Exception:
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
            schedule = self._client.presence_templates_next.get(childname, {})

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


class AulaClosedDaysSensor(Entity):
    """Sensor showing closed days for institutions."""

    def __init__(self, hass, coordinator) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        return "Aula Closed Days"

    @property
    def state(self):
        try:
            # Find the next upcoming closed day
            today = datetime.now().date()
            next_closed_day = None
            next_closed_info = None

            for institution_code, closed_days in self._client.closed_days.items():
                for closed_day in closed_days:
                    try:
                        start_date = parse(closed_day["startDate"]).date()
                        end_date = parse(closed_day["endDate"]).date()

                        # Check if this closed period is upcoming or ongoing
                        if end_date >= today:
                            # If this is sooner than our current next_closed_day, use it
                            if next_closed_day is None or start_date < next_closed_day:
                                next_closed_day = start_date
                                next_closed_info = {
                                    "name": closed_day["name"],
                                    "start_date": start_date.isoformat(),
                                    "end_date": end_date.isoformat(),
                                    "institution": institution_code,
                                }
                    except Exception as e:
                        _LOGGER.debug(f"Error parsing closed day date: {e}")
                        continue

            if next_closed_info:
                # Calculate days until closure
                days_until = (next_closed_day - today).days
                if days_until == 0:
                    return "Today"
                elif days_until == 1:
                    return "Tomorrow"
                elif days_until > 0:
                    return f"{days_until} days"
                else:
                    return "Ongoing"
            else:
                return "None scheduled"

        except Exception as e:
            _LOGGER.debug(f"Error in closed days sensor state: {e}")
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            # Add next upcoming closed day details
            today = datetime.now().date()
            next_closed_day = None
            next_closed_info = None

            # Collect all closed days for attributes
            all_closed_days = []
            upcoming_closed_days = []

            for institution_code, closed_days in self._client.closed_days.items():
                # Get institution name from child data if available
                institution_name = institution_code
                for child_id, inst_code in getattr(
                    self._client, "_institutions", {}
                ).items():
                    if inst_code and institution_code in str(inst_code):
                        institution_name = inst_code
                        break

                for closed_day in closed_days:
                    try:
                        start_date = parse(closed_day["startDate"]).date()
                        end_date = parse(closed_day["endDate"]).date()

                        closed_day_info = {
                            "name": closed_day["name"],
                            "start_date": start_date.isoformat(),
                            "end_date": end_date.isoformat(),
                            "institution_code": institution_code,
                            "institution_name": institution_name,
                            "id": closed_day.get("id", ""),
                        }

                        all_closed_days.append(closed_day_info)

                        # Check if this is upcoming or ongoing
                        if end_date >= today:
                            upcoming_closed_days.append(closed_day_info)

                            # Track the very next one for detailed attributes
                            if next_closed_day is None or start_date < next_closed_day:
                                next_closed_day = start_date
                                next_closed_info = closed_day_info

                    except Exception as e:
                        _LOGGER.debug(f"Error processing closed day: {e}")
                        continue

            # Sort by start date
            all_closed_days.sort(key=lambda x: x["start_date"])
            upcoming_closed_days.sort(key=lambda x: x["start_date"])

            # Add detailed attributes
            if next_closed_info:
                attributes["next_closure_name"] = next_closed_info["name"]
                attributes["next_closure_start"] = next_closed_info["start_date"]
                attributes["next_closure_end"] = next_closed_info["end_date"]
                attributes["next_closure_institution"] = next_closed_info[
                    "institution_name"
                ]

                days_until = (next_closed_day - today).days
                attributes["days_until_next_closure"] = days_until

            attributes["total_closed_days"] = len(all_closed_days)
            attributes["upcoming_closures_count"] = len(upcoming_closed_days)
            attributes["all_closed_days"] = all_closed_days[
                :20
            ]  # Limit to prevent too much data
            attributes["upcoming_closed_days"] = upcoming_closed_days[
                :10
            ]  # Limit upcoming ones

        except Exception as e:
            _LOGGER.debug(f"Error in closed days sensor attributes: {e}")
            attributes["error"] = str(e)

        return attributes

    @property
    def unique_id(self):
        return "aula_closed_days"

    @property
    def icon(self):
        return "mdi:calendar-remove"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success and bool(
            getattr(self._client, "closed_days", {})
        )

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class AulaWeeklyPresenceSensor(Entity):
    """Weekly presence sensor (komme og gå) for current week."""

    def __init__(self, hass, coordinator, child):
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Weekly Presence - {childname}"

    @property
    def state(self):
        try:
            child_id = str(self._child["id"])
            week_data = self._client.weekly_presence_current.get(child_id, {})

            _LOGGER.debug(
                f"Weekly presence sensor for child {child_id}: week_data = {week_data}"
            )

            if not week_data:
                return "No presence data"

            # Count days with actual check-in/check-out data
            days_with_presence = 0
            for date, day_data in week_data.items():
                if day_data.get("check_in_time") or day_data.get("check_out_time"):
                    days_with_presence += 1

            if days_with_presence == 0:
                return "Present but no times"

            return f"{days_with_presence} day(s)"

        except Exception as e:
            _LOGGER.debug(
                f"Error in weekly presence sensor state for child {self._child['id']}: {e}"
            )
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            child_id = str(self._child["id"])
            week_data = self._client.weekly_presence_current.get(child_id, {})

            _LOGGER.debug(
                f"Weekly presence attributes for child {child_id}: found {len(week_data)} days"
            )

            # Add summary information
            if week_data:
                total_days = len(week_data)
                present_days = 0
                earliest_arrival = None
                latest_departure = None

                for date, day_data in week_data.items():
                    check_in = day_data.get("check_in_time")
                    check_out = day_data.get("check_out_time")

                    if check_in or check_out:
                        present_days += 1

                    if check_in:
                        if earliest_arrival is None or check_in < earliest_arrival:
                            earliest_arrival = check_in

                    if check_out:
                        if latest_departure is None or check_out > latest_departure:
                            latest_departure = check_out

                attributes["total_days"] = total_days
                attributes["present_days"] = present_days
                attributes["earliest_arrival"] = earliest_arrival
                attributes["latest_departure"] = latest_departure
            else:
                attributes["total_days"] = 0
                attributes["present_days"] = 0

            # Add detailed daily data (limited to prevent overflow)
            daily_data = {}
            for date, day_data in sorted(week_data.items())[:7]:  # Limit to 7 days
                daily_info = {
                    "day": day_data.get("day_name", ""),
                    "check_in": day_data.get("check_in_time"),
                    "check_out": day_data.get("check_out_time"),
                    "planned_entry": day_data.get("planned_entry_time"),
                    "planned_exit": day_data.get("planned_exit_time"),
                    "exit_with": day_data.get("exit_with"),
                    "status": day_data.get("status"),
                    "comment": day_data.get("comment"),
                    "institution": day_data.get("institution_name"),
                    "group": day_data.get("main_group"),
                }
                daily_data[date] = daily_info

            attributes["daily_data"] = daily_data

        except Exception as e:
            _LOGGER.debug(f"Error in weekly presence sensor attributes: {e}")
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
        return f"weekly_presence_{childname}"

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


class AulaWeeklyPresenceNextSensor(Entity):
    """Weekly presence sensor (komme og gå) for next week."""

    def __init__(self, hass, coordinator, child):
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Weekly Presence Next Week - {childname}"

    @property
    def state(self):
        try:
            child_id = str(self._child["id"])
            week_data = self._client.weekly_presence_next.get(child_id, {})

            _LOGGER.debug(
                f"Next week presence sensor for child {child_id}: week_data = {week_data}"
            )

            if not week_data:
                return "No presence data"

            # Count days with actual check-in/check-out data or planned times
            days_with_presence = 0
            for date, day_data in week_data.items():
                if (
                    day_data.get("check_in_time")
                    or day_data.get("check_out_time")
                    or day_data.get("planned_entry_time")
                    or day_data.get("planned_exit_time")
                ):
                    days_with_presence += 1

            if days_with_presence == 0:
                return "Present but no times"

            return f"{days_with_presence} day(s)"

        except Exception as e:
            _LOGGER.debug(
                f"Error in next week presence sensor state for child {self._child['id']}: {e}"
            )
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            child_id = str(self._child["id"])
            week_data = self._client.weekly_presence_next.get(child_id, {})

            _LOGGER.debug(
                f"Next week presence attributes for child {child_id}: found {len(week_data)} days"
            )

            # Add summary information
            if week_data:
                total_days = len(week_data)
                scheduled_days = 0
                earliest_planned = None
                latest_planned = None

                for date, day_data in week_data.items():
                    planned_entry = day_data.get("planned_entry_time")
                    planned_exit = day_data.get("planned_exit_time")

                    if planned_entry or planned_exit:
                        scheduled_days += 1

                    if planned_entry:
                        if earliest_planned is None or planned_entry < earliest_planned:
                            earliest_planned = planned_entry

                    if planned_exit:
                        if latest_planned is None or planned_exit > latest_planned:
                            latest_planned = planned_exit

                attributes["total_days"] = total_days
                attributes["scheduled_days"] = scheduled_days
                attributes["earliest_planned"] = earliest_planned
                attributes["latest_planned"] = latest_planned
            else:
                attributes["total_days"] = 0
                attributes["scheduled_days"] = 0

            # Add detailed daily data (limited to prevent overflow)
            daily_data = {}
            for date, day_data in sorted(week_data.items())[:7]:  # Limit to 7 days
                daily_info = {
                    "day": day_data.get("day_name", ""),
                    "check_in": day_data.get("check_in_time"),
                    "check_out": day_data.get("check_out_time"),
                    "planned_entry": day_data.get("planned_entry_time"),
                    "planned_exit": day_data.get("planned_exit_time"),
                    "exit_with": day_data.get("exit_with"),
                    "status": day_data.get("status"),
                    "comment": day_data.get("comment"),
                    "institution": day_data.get("institution_name"),
                    "group": day_data.get("main_group"),
                }
                daily_data[date] = daily_info

            attributes["daily_data"] = daily_data

        except Exception as e:
            _LOGGER.debug(f"Error in next week presence sensor attributes: {e}")
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
        return f"weekly_presence_next_{childname}"

    @property
    def icon(self):
        return "mdi:calendar-clock-outline"

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


class AulaPostsSensor(Entity):
    """Sensor for Aula posts/news (indlæg)"""

    def __init__(self, hass, coordinator, child):
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Posts {childname}"

    @property
    def state(self):
        try:
            child_id = str(self._child["id"])
            posts = self._client.posts_by_child.get(child_id, [])

            _LOGGER.debug(f"POSTS SENSOR: Child {child_id} has {len(posts)} posts")

            # Return total posts count instead of recent posts
            return len(posts)

        except Exception as e:
            _LOGGER.error(f"POSTS SENSOR: Error getting posts sensor state: {e}")
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            child_id = str(self._child["id"])
            posts = self._client.posts_by_child.get(child_id, [])

            _LOGGER.debug(
                f"POSTS SENSOR: Processing {len(posts)} posts for child {child_id}"
            )

            # Get recent posts (last 30 days) with details (limit to 5 most recent)
            from datetime import datetime, timedelta, timezone

            cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)
            recent_posts = []
            important_posts = []

            for post in posts[:5]:  # Limit to 5 most recent for cleaner output
                try:
                    # Handle timestamp with proper timezone parsing
                    timestamp_str = post["timestamp"]
                    if timestamp_str.endswith("Z"):
                        timestamp_str = timestamp_str[:-1] + "+00:00"
                    elif not timestamp_str.endswith(
                        "+00:00"
                    ) and not timestamp_str.endswith("00:00"):
                        timestamp_str += "+00:00"

                    post_date = datetime.fromisoformat(timestamp_str)

                    post_summary = {
                        "title": post["title"],
                        "content_preview": post["content"]["text"][:100]
                        + ("..." if len(post["content"]["text"]) > 100 else ""),
                        "timestamp": post["timestamp"],
                    }

                    if post_date >= cutoff_date:
                        recent_posts.append(post_summary)

                    if post["is_important"]:
                        important_posts.append(post_summary)

                except (ValueError, TypeError, KeyError) as e:
                    _LOGGER.debug(
                        f"POSTS SENSOR: Error processing post {post.get('id', 'unknown')}: {e}"
                    )
                    continue

            attributes["recent_posts"] = recent_posts
            attributes["important_posts"] = important_posts
            attributes["total_posts"] = len(posts)
            attributes["recent_count"] = len(recent_posts)
            attributes["important_count"] = len(important_posts)

            # Latest post basic info
            if posts:
                latest_post = posts[0]  # Posts should be sorted by timestamp
                attributes["latest_post"] = {
                    "title": latest_post["title"],
                    "timestamp": latest_post["timestamp"],
                }

            _LOGGER.debug(
                f"POSTS SENSOR: Set attributes - total: {len(posts)}, recent: {len(recent_posts)}, important: {len(important_posts)}"
            )

        except Exception as e:
            _LOGGER.error(f"POSTS SENSOR: Error in posts sensor attributes: {e}")
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
        return f"posts_{childname}"

    @property
    def device_class(self):
        return None

    @property
    def unit_of_measurement(self):
        return "posts"

    @property
    def icon(self):
        return "mdi:post-outline"

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


class AulaMailSensor(Entity):
    """Sensor for Aula mail threads"""

    def __init__(self, hass, coordinator, child):
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Mail {childname}"

    @property
    def state(self):
        try:
            child_id = str(self._child["id"])
            mail_threads = self._client.mail_by_child.get(child_id, [])

            _LOGGER.debug(
                f"MAIL SENSOR: Child {child_id} has {len(mail_threads)} mail threads"
            )
            _LOGGER.debug(
                f"MAIL SENSOR: Available mail children: {list(self._client.mail_by_child.keys())}"
            )

            # Return total mail threads count
            return len(mail_threads)

        except Exception as e:
            _LOGGER.error(
                f"MAIL SENSOR: Error getting state for child {self._child.get('id', 'unknown')}: {e}"
            )
            return 0

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            child_id = str(self._child["id"])
            mail_threads = self._client.mail_by_child.get(child_id, [])

            _LOGGER.debug(
                f"MAIL SENSOR: Processing {len(mail_threads)} mail threads for child {child_id}"
            )

            # Get recent threads (limit to 5 most recent)
            recent_threads = []
            unread_count = 0
            marked_count = 0

            for thread in mail_threads[:5]:  # Limit to 5 most recent for cleaner output
                try:
                    thread_summary = {
                        "subject": thread["subject"],
                        "creator": thread["creator"].get("full_name", "Unknown"),
                        "started_time": thread["started_time"],
                        "read": thread["read"],
                        "marked": thread["marked"],
                        "sensitive": thread["sensitive"],
                        "latest_message_preview": thread["latest_message"].get(
                            "text_clean", ""
                        )[:100]
                        + (
                            "..."
                            if len(thread["latest_message"].get("text_clean", "")) > 100
                            else ""
                        ),
                    }

                    recent_threads.append(thread_summary)

                except (ValueError, TypeError, KeyError) as e:
                    _LOGGER.debug(
                        f"MAIL SENSOR: Error processing thread {thread.get('id', 'unknown')}: {e}"
                    )
                    continue

            # Count unread and marked across all threads
            for thread in mail_threads:
                if not thread.get("read", True):
                    unread_count += 1
                if thread.get("marked", False):
                    marked_count += 1

            # Set attributes
            attributes["recent_threads"] = recent_threads
            attributes["total_threads"] = len(mail_threads)
            attributes["unread_count"] = unread_count
            attributes["marked_count"] = marked_count

            # Latest thread basic info
            if mail_threads:
                latest_thread = mail_threads[0]  # Threads should be sorted by date
                attributes["latest_thread"] = {
                    "subject": latest_thread["subject"],
                    "started_time": latest_thread["started_time"],
                    "creator": latest_thread["creator"].get("full_name", "Unknown"),
                    "read": latest_thread["read"],
                }

            _LOGGER.debug(
                f"MAIL SENSOR: Set attributes - total: {len(mail_threads)}, unread: {unread_count}, marked: {marked_count}"
            )

        except Exception as e:
            _LOGGER.error(f"MAIL SENSOR: Error in mail sensor attributes: {e}")
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
        return f"mail_{childname}"

    @property
    def device_class(self):
        return None

    @property
    def unit_of_measurement(self):
        return "threads"

    @property
    def icon(self):
        return "mdi:email-outline"

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

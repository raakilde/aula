"""Weekly schedule sensors for Aula integration."""

import logging

from homeassistant.helpers.entity import Entity

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

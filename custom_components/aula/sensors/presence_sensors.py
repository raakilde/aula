"""Weekly presence (komme og gå) sensors for Aula integration."""

import logging

from homeassistant.helpers.entity import Entity

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

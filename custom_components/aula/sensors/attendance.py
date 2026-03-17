"""Attendance/presence sensor for Aula integration."""

import logging
from datetime import datetime

from homeassistant.helpers.entity import Entity

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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
            except (KeyError, TypeError):
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
                    except (ValueError, KeyError, TypeError):
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

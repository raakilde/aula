"""Closed days sensor for Aula integration."""

import logging
from datetime import datetime

from dateutil.parser import parse
from homeassistant.helpers.entity import Entity

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

"""Week plan and reminder sensors for Aula integration."""

import logging

from homeassistant.helpers.entity import Entity

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

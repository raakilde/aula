from datetime import datetime, timedelta
import json
import logging, time
from zoneinfo import ZoneInfo
from .const import DOMAIN
from homeassistant import config_entries, core
from .const import CONF_MINUDANNELSEOPGAVELISTE
from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.util import Throttle

_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=10)
PARALLEL_UPDATES = 1


class TaskListDevice(CalendarEntity):
    def __init__(self, hass, calendar, name, childid):
        self.data = TaskListData(hass, calendar, childid)
        self._cal_data = {}
        self._name = "Opgavelist " + name
        self._childid = childid

    @property
    def event(self):
        """Return the next upcoming event."""
        return self.data.event

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self):
        unique_id = "aulatasklist" + str(self._childid)
        _LOGGER.debug("Unique ID for tasklist " + str(self._childid) + " " + unique_id)
        return unique_id

    def update(self):
        """Update all Calendars."""
        self.data.update()

    async def async_get_events(self, hass, start_date, end_date):
        """Get all events in a specific time frame."""
        return await self.data.async_get_events(hass, start_date, end_date)


class TaskListData:
    def __init__(self, hass, calendar, childid):
        self.event = None

        self._hass = hass
        self._calendar = calendar
        self._childid = childid

        self.all_events = []
        self._client = hass.data[DOMAIN]["client"]

    def parseTaskListData(self, i=None):
        events = {}

        try:
            with open("uddannelseopgaveliste.json", "r") as openfile:
                tasks = json.load(openfile)
        except:
            _LOGGER.warn("Could not open and parse file uddannelseopgaveliste.json!")
            return False
        events = []
        _LOGGER.debug("Parsing skoleskema.json...")
        for task in tasks:
            end = datetime.strptime(
                task["afleveringsdato"], "%Y-%m-%dT%H:%M:%S.%f0"
            ).replace(tzinfo=ZoneInfo("Europe/Berlin"), hour=8, minute=5)
            start = end - timedelta(minutes=5)

            if task["placeringTidspunkt"] is not None:
                start = datetime.strptime(
                    task["placeringTidspunkt"], "%Y-%m-%dT%H:%M:%S.%f0"
                ).replace(tzinfo=ZoneInfo("Europe/Berlin"))
                end = start + timedelta(seconds=1)

            summary = task["title"]
            try:
                event = CalendarEvent(
                    summary=summary,
                    start=start,
                    end=end,
                )
                events.append(event)
            except:
                _LOGGER.warn("dd")

        return events

    async def async_get_events(self, hass, start_date, end_date):
        events = self.parseTaskListData()
        return events

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        _LOGGER.debug("Updating calendars...")
        self.parseTaskListData(self)

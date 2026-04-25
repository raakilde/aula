import logging
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant import config_entries, core
from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.util import Throttle

from .const import CONF_SCHOOLSCHEDULE, DOMAIN
from .tasklist import TaskListDevice

_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=30)
PARALLEL_UPDATES = 1


class _CalendarFileCache:
    """Cache parsed school schedule events across calendar entities."""

    _path: str | None = None
    _mtime: float | None = None
    _events_by_child: dict[int, list[CalendarEvent]] = {}

    @classmethod
    def get_events(cls, path: str) -> dict[int, list[CalendarEvent]] | None:
        import json

        file_path = Path(path)
        try:
            stat = file_path.stat()
        except OSError as err:
            _LOGGER.warning("Could not access skoleskema.json: %s", err)
            return None

        if cls._path == path and cls._mtime == stat.st_mtime:
            return cls._events_by_child

        try:
            with file_path.open("r") as openfile:
                raw_data = json.load(openfile)
            data = json.loads(raw_data)
        except (OSError, json.JSONDecodeError, ValueError) as err:
            _LOGGER.warning("Could not open and parse file skoleskema.json: %s", err)
            return None

        events_by_child: dict[int, list[CalendarEvent]] = {}
        for calendar_entry in data.get("data", []):
            if calendar_entry.get("type") != "lesson":
                continue

            belongs_to = calendar_entry.get("belongsToProfiles") or []
            if not belongs_to:
                continue

            child_id = belongs_to[0]
            summary = calendar_entry["title"]
            start = datetime.strptime(
                calendar_entry["startDateTime"], "%Y-%m-%dT%H:%M:%S%z"
            )
            end = datetime.strptime(
                calendar_entry["endDateTime"], "%Y-%m-%dT%H:%M:%S%z"
            )

            teacher = ""
            for participant in calendar_entry["lesson"]["participants"]:
                if participant["participantRole"] == "substituteTeacher":
                    teacher = "VIKAR: " + participant["teacherName"]
                    break

            if not teacher:
                try:
                    teacher = calendar_entry["lesson"]["participants"][0][
                        "teacherInitials"
                    ]
                except (KeyError, IndexError, TypeError):
                    try:
                        _LOGGER.debug(
                            "Lesson json dump%s", str(calendar_entry["lesson"])
                        )
                        teacher = calendar_entry["lesson"]["participants"][0][
                            "teacherName"
                        ]
                    except (KeyError, IndexError, TypeError):
                        _LOGGER.debug(
                            "Could not find any teacher information for %s at %s",
                            summary,
                            start,
                        )

            event = CalendarEvent(
                summary=summary + ", " + teacher,
                start=start,
                end=end,
            )
            events_by_child.setdefault(child_id, []).append(event)

        cls._path = path
        cls._mtime = stat.st_mtime
        cls._events_by_child = events_by_child
        _LOGGER.debug("Reloaded skoleskema.json into shared calendar cache")
        return events_by_child


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    config = hass.data[DOMAIN][config_entry.entry_id]
    if config_entry.options:
        config.update(config_entry.options)

    if not config[CONF_SCHOOLSCHEDULE] == True:
        return True
    client = hass.data[DOMAIN]["client"]
    calendar_devices = []
    calendar = []
    for i, child in enumerate(client._children):
        childid = child["id"]
        name = child["name"]
        calendar_devices.append(CalendarDevice(hass, calendar, name, childid))
        calendar_devices.append(TaskListDevice(hass, calendar, name, childid))

    async_add_entities(calendar_devices)


class CalendarDevice(CalendarEntity):
    def __init__(self, hass, calendar, name, childid):
        self.data = CalendarData(hass, calendar, childid)
        self._cal_data = {}
        self._name = "Schedule - " + name.split()[0]
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
        childname = self._name.split(" - ")[1].lower().replace(" ", "_")
        unique_id = f"schedule_{childname}"
        _LOGGER.debug("Unique ID for calendar " + str(self._childid) + " " + unique_id)
        return unique_id

    def update(self):
        """Update all Calendars."""
        self.data.update()

    async def async_get_events(self, hass, start_date, end_date):
        """Get all events in a specific time frame."""
        return await self.data.async_get_events(hass, start_date, end_date)


class CalendarData:
    def __init__(self, hass, calendar, childid):
        self.event = None

        self._hass = hass
        self._calendar = calendar
        self._childid = childid

        self.all_events = []
        self._client = hass.data[DOMAIN]["client"]

    def parseCalendarData(self, i=None):
        events_by_child = _CalendarFileCache.get_events(
            self._client._data_path("skoleskema.json")
        )
        if events_by_child is None:
            return False
        return events_by_child.get(self._childid, [])

    async def async_get_events(self, hass, start_date, end_date):
        events = self.parseCalendarData()
        return events

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        self.all_events = self.parseCalendarData(self) or []

import logging
from datetime import timedelta

from homeassistant import config_entries, core
from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Avoid default 30s polling; align with coordinator refresh cadence.
SCAN_INTERVAL = timedelta(minutes=30)


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):

    client = hass.data[DOMAIN]["client"]
    if client.unread_messages == 1:
        try:
            subject = client.message["subject"]
        except (KeyError, TypeError):
            subject = ""
        try:
            text = client.message["text"]
        except (KeyError, TypeError):
            text = ""
        try:
            sender = client.message["sender"]
        except (KeyError, TypeError):
            sender = ""
    else:
        subject = ""
        text = ""
        sender = ""

    sensors = []
    device = AulaBinarySensor(
        hass=hass,
        unread=client.unread_messages,
        subject=subject,
        text=text,
        sender=sender,
    )
    sensors.append(device)
    async_add_entities(sensors, True)


class AulaBinarySensor(BinarySensorEntity):
    def __init__(self, hass, unread, subject, text, sender):
        self._hass = hass
        self._unread = unread
        self._subject = subject
        self._text = text
        self._sender = sender
        self._client = self._hass.data[DOMAIN]["client"]
        # Keep internal binary state stable from startup.
        self._state = 1 if unread == 1 else 0
        self._last_logged_state = None

    @property
    def extra_state_attributes(self):
        attributes = {}
        attributes["subject"] = self._subject
        attributes["text"] = self._text
        attributes["sender"] = self._sender
        attributes["friendly_name"] = "Aula message"
        return attributes

    @property
    def unique_id(self):
        unique_id = "aulamessage"
        return unique_id

    @property
    def icon(self):
        return "mdi:email"

    @property
    def is_on(self):
        if self._state == 1:
            return True
        if self._state == 0:
            return False

    def update(self):
        if self._client.unread_messages == 1:
            if self._last_logged_state != 1:
                _LOGGER.debug("There are unread message(s)")
                self._last_logged_state = 1
            # _LOGGER.debug("Latest message: "+str(self._client.message))
            self._subject = self._client.message["subject"]
            self._text = self._client.message["text"]
            self._sender = self._client.message["sender"]
            self._state = 1
        else:
            if self._last_logged_state != 0:
                _LOGGER.debug("There are NO unread messages")
                self._last_logged_state = 0
            self._state = 0
            self._subject = ""
            self._text = ""
            self._sender = ""

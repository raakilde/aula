"""Sensor entity classes for the Aula integration."""

from .attendance import AulaAttendanceSensor
from .closed_days import AulaClosedDaysSensor
from .communication import AulaMailSensor, AulaPostsSensor
from .education import AulaEducationSensor, AulaWeekNotesSensor
from .library import AulaLibrarySensor
from .presence_sensors import AulaWeeklyPresenceNextSensor, AulaWeeklyPresenceSensor
from .schedule import AulaWeeklyScheduleNextSensor, AulaWeeklyScheduleSensor
from .weekplan import AulaReminderSensor, AulaWeekPlanSensor

__all__ = [
    "AulaAttendanceSensor",
    "AulaLibrarySensor",
    "AulaEducationSensor",
    "AulaWeekNotesSensor",
    "AulaWeekPlanSensor",
    "AulaReminderSensor",
    "AulaWeeklyScheduleSensor",
    "AulaWeeklyScheduleNextSensor",
    "AulaClosedDaysSensor",
    "AulaWeeklyPresenceSensor",
    "AulaWeeklyPresenceNextSensor",
    "AulaPostsSensor",
    "AulaMailSensor",
]

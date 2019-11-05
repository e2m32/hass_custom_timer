"""Support for Timers."""
from datetime import timedelta, datetime, timezone
import logging

import voluptuous as vol

from homeassistant.const import CONF_ICON, CONF_NAME
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.config_validation import ENTITY_SERVICE_SCHEMA
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.restore_state import RestoreEntity
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

DOMAIN = "timer"
ENTITY_ID_FORMAT = DOMAIN + ".{}"

DEFAULT_DURATION = 0
DEFAULT_RESTORE = False
DEFAULT_RESTORE_TIMEOUT = timedelta(minutes=15)
ATTR_DURATION = "duration"
ATTR_REMAINING = "remaining"
ATTR_RESTORE = "restore"
ATTR_RESTORE_TIMEOUT = "restore_timeout"
ATTR_END = "end"
CONF_DURATION = "duration"
CONF_RESTORE = "restore"
CONF_RESTORE_TIMEOUT = "restore_timeout"

STATUS_IDLE = "idle"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"

VIABLE_STATUSES = [STATUS_IDLE, STATUS_ACTIVE, STATUS_PAUSED ]

EVENT_TIMER_FINISHED = "timer.finished"
EVENT_TIMER_CANCELLED = "timer.cancelled"
EVENT_TIMER_STARTED = "timer.started"
EVENT_TIMER_RESTARTED = "timer.restarted"
EVENT_TIMER_PAUSED = "timer.paused"

SERVICE_START = "start"
SERVICE_PAUSE = "pause"
SERVICE_CANCEL = "cancel"
SERVICE_FINISH = "finish"

SERVICE_SCHEMA_DURATION = ENTITY_SERVICE_SCHEMA.extend(
    {vol.Optional(ATTR_DURATION, default=timedelta(DEFAULT_DURATION)): cv.time_period}
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: cv.schema_with_slug_keys(
            vol.Any(
                {
                    vol.Optional(CONF_NAME): cv.string,
                    vol.Optional(CONF_ICON): cv.icon,
                    vol.Optional(
                        CONF_DURATION, timedelta(DEFAULT_DURATION)
                    ): cv.time_period,
                    vol.Optional(
                        CONF_RESTORE, DEFAULT_RESTORE
                    ): cv.boolean,
                    vol.Optional(
                        CONF_RESTORE_TIMEOUT, DEFAULT_RESTORE_TIMEOUT
                    ): cv.time_period,
                },
                None,
            )
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config):
    """Set up a timer."""
    component = EntityComponent(_LOGGER, DOMAIN, hass)

    entities = []

    for object_id, cfg in config[DOMAIN].items():
        if not cfg:
            cfg = {}

        name = cfg.get(CONF_NAME)
        icon = cfg.get(CONF_ICON)
        duration = cfg.get(CONF_DURATION)
        restore = cfg.get(CONF_RESTORE)
        restore_timeout = cfg.get(CONF_RESTORE_TIMEOUT)

        entities.append(Timer(hass, object_id, name, icon, duration, restore, restore_timeout))

    if not entities:
        return False

    component.async_register_entity_service(
        SERVICE_START, SERVICE_SCHEMA_DURATION, "async_start"
    )
    component.async_register_entity_service(
        SERVICE_PAUSE, ENTITY_SERVICE_SCHEMA, "async_pause"
    )
    component.async_register_entity_service(
        SERVICE_CANCEL, ENTITY_SERVICE_SCHEMA, "async_cancel"
    )
    component.async_register_entity_service(
        SERVICE_FINISH, ENTITY_SERVICE_SCHEMA, "async_finish"
    )

    await component.async_add_entities(entities)
    return True


class Timer(RestoreEntity):
    """Representation of a timer."""

    def __init__(self, hass, object_id, name, icon, duration, restore, restore_timeout):
        """Initialize a timer."""
        self.entity_id = ENTITY_ID_FORMAT.format(object_id)
        self._name = name
        self._state = STATUS_IDLE
        self._duration = duration
        self._remaining = self._duration
        self._restore = restore if restore is not None \
                        else DEFAULT_RESTORE
        if self._restore:
            self._restore_timeout = restore_timeout \
                                    if restore_timeout is not None \
                                    else DEFAULT_RESTORE_TIMEOUT
        else:
            self._restore_timeout = None
        
        self._icon = icon
        self._hass = hass
        self._end = None
        self._listener = None

    @property
    def should_poll(self):
        """If entity should be polled."""
        return False

    @property
    def name(self):
        """Return name of the timer."""
        return self._name

    @property
    def icon(self):
        """Return the icon to be used for this entity."""
        return self._icon

    @property
    def state(self):
        """Return the current value of the timer."""
        return self._state

    @property
    def state_attributes(self):
        """Return the state attributes."""
        return {
            ATTR_DURATION: str(self._duration),
            ATTR_REMAINING: str(self._remaining),
            ATTR_RESTORE: str(self._restore),
            ATTR_RESTORE_TIMEOUT: str(self._restore_timeout),
            ATTR_END: str(self._end.replace(tzinfo=timezone.utc).astimezone(tz=None)) \
                      if self._end is not None \
                      else None,
        }

    async def async_added_to_hass(self):
        """Call when entity is about to be added to Home Assistant."""
        
        if not self._restore:
            self._state = STATUS_IDLE
            return
        
        # Check for previous recorded state
        state = await self.async_get_last_state()
        if state is not None:
            for check_status in VIABLE_STATUSES:
                if state.state == check_status:
                    self._state = state.state
                    # restore last duration if config doesn't have a default
                    if not self._duration and not state.attributes.get(ATTR_DURATION) == "None":
                        duration_data = list(map(int, str(state.attributes.get(ATTR_DURATION)).split(":")))
                        self._duration = timedelta(hours=duration_data[0], 
                                                   minutes=duration_data[1], 
                                                   seconds=duration_data[2])
                    # restore remaining (needed for paused state)
                    if self._state == STATUS_PAUSED \
                       and not state.attributes.get(ATTR_REMAINING) == "None" \
                       and not state.attributes.get(ATTR_REMAINING) == str(timedelta()):
                        remaining_dt = datetime.strptime(state.attributes.get(ATTR_REMAINING), "%H:%M:%S.%f")
                        self._remaining = timedelta(hours=remaining_dt.hour,
                                                    minutes=remaining_dt.minute,
                                                    seconds=remaining_dt.second)
                    else:
                        self._remaining = timedelta()
                    self._end = datetime.strptime(state.attributes.get(ATTR_END), "%Y-%m-%d %H:%M:%S.%f%z") \
                                if not state.attributes.get(ATTR_END) == "None" \
                                   and state.attributes.get(ATTR_END) is not None \
                                else None

                    if self._state == STATUS_ACTIVE:
                        self._remaining = self._end - dt_util.utcnow()
                        # Only restore if restore_timeout not exceeded
                        if self._remaining + self._restore_timeout >= timedelta():
                            self._state = STATUS_PAUSED
                            self._end = None
                            await self.async_start(None)
                        else:
                            self._state = STATUS_IDLE
                    return
        # Set state to IDLE if no recorded state, or invalid
        self._state = STATUS_IDLE

    async def async_start(self, duration):
        """Start a timer."""
        if self._listener:
            self._listener()
            self._listener = None
        newduration = None
        if duration:
            newduration = duration

        event = EVENT_TIMER_STARTED
        if self._state == STATUS_PAUSED:
            event = EVENT_TIMER_RESTARTED

        self._state = STATUS_ACTIVE
        # pylint: disable=redefined-outer-name
        start = dt_util.utcnow()
        if self._remaining and newduration is None:
            self._end = start + self._remaining
        else:
            if newduration:
                self._duration = newduration
                self._remaining = newduration
            else:
                self._remaining = self._duration
            self._end = start + self._duration

        self._hass.bus.async_fire(event, {"entity_id": self.entity_id})

        self._listener = async_track_point_in_utc_time(
            self._hass, self.async_finished, self._end
        )
        await self.async_update_ha_state()

    async def async_pause(self):
        """Pause a timer."""
        if self._listener is None:
            return

        self._listener()
        self._listener = None
        self._remaining = self._end - dt_util.utcnow()
        self._state = STATUS_PAUSED
        self._end = None
        self._hass.bus.async_fire(EVENT_TIMER_PAUSED, {"entity_id": self.entity_id})
        await self.async_update_ha_state()

    async def async_cancel(self):
        """Cancel a timer."""
        if self._listener:
            self._listener()
            self._listener = None
        self._state = STATUS_IDLE
        self._end = None
        self._remaining = timedelta()
        self._hass.bus.async_fire(EVENT_TIMER_CANCELLED, {"entity_id": self.entity_id})
        await self.async_update_ha_state()

    async def async_finish(self):
        """Reset and updates the states, fire finished event."""
        if self._state != STATUS_ACTIVE:
            return

        self._listener = None
        self._state = STATUS_IDLE
        self._remaining = timedelta()
        self._hass.bus.async_fire(EVENT_TIMER_FINISHED, {"entity_id": self.entity_id})
        await self.async_update_ha_state()

    async def async_finished(self, time):
        """Reset and updates the states, fire finished event."""
        if self._state != STATUS_ACTIVE:
            return

        self._listener = None
        self._state = STATUS_IDLE
        self._remaining = timedelta()
        self._hass.bus.async_fire(EVENT_TIMER_FINISHED, {"entity_id": self.entity_id})
        await self.async_update_ha_state()

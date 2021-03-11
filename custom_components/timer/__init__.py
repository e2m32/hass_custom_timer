"""Support for Timers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Dict, Optional

import voluptuous as vol

from homeassistant.const import (
    ATTR_EDITABLE,
    CONF_ICON,
    CONF_ID,
    CONF_NAME,
    SERVICE_RELOAD,
)
from homeassistant.core import callback
from homeassistant.helpers import collection
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.restore_state import RestoreEntity
import homeassistant.helpers.service
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType, HomeAssistantType, ServiceCallType
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

DOMAIN = "timer"
ENTITY_ID_FORMAT = DOMAIN + ".{}"

DEFAULT_DURATION = 0
DEFAULT_RESTORE = True
DEFAULT_RESTORE_GRACE_PERIOD = 0
ATTR_DURATION = "duration"
ATTR_REMAINING = "remaining"
ATTR_FINISHES_AT = "finishes_at"
ATTR_RESTORE = "restore"
ATTR_RESTORE_GRACE_PERIOD = "restore_grace_period"
CONF_DURATION = "duration"
CONF_RESTORE = "restore"
CONF_RESTORE_GRACE_PERIOD = "restore_grace_period"

STATUS_IDLE = "idle"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"

VIABLE_STATUSES = [STATUS_IDLE, STATUS_ACTIVE, STATUS_PAUSED]

EVENT_TIMER_FINISHED = "timer.finished"
EVENT_TIMER_CANCELLED = "timer.cancelled"
EVENT_TIMER_STARTED = "timer.started"
EVENT_TIMER_RESTARTED = "timer.restarted"
EVENT_TIMER_PAUSED = "timer.paused"

SERVICE_START = "start"
SERVICE_PAUSE = "pause"
SERVICE_CANCEL = "cancel"
SERVICE_FINISH = "finish"

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

CREATE_FIELDS = {
    vol.Required(CONF_NAME): vol.All(str, vol.Length(min=1)),
    vol.Optional(CONF_NAME): cv.string,
    vol.Optional(CONF_ICON): cv.icon,
    vol.Optional(CONF_DURATION, default=DEFAULT_DURATION): cv.time_period,
    vol.Optional(CONF_RESTORE, default=DEFAULT_RESTORE): cv.boolean,
    vol.Optional(CONF_RESTORE_GRACE_PERIOD, default=DEFAULT_RESTORE_GRACE_PERIOD): cv.time_period,
}
UPDATE_FIELDS = {
    vol.Optional(CONF_NAME): cv.string,
    vol.Optional(CONF_ICON): cv.icon,
    vol.Optional(CONF_DURATION): cv.time_period,
    vol.Optional(CONF_RESTORE): cv.boolean,
    vol.Optional(CONF_RESTORE_GRACE_PERIOD): cv.time_period,
}


def _format_timedelta(delta: timedelta):
    total_seconds = delta.total_seconds()
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours)}:{int(minutes):02}:{int(seconds):02}"


def _none_to_empty_dict(value):
    if value is None:
        return {}
    return value


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: cv.schema_with_slug_keys(
            vol.All(
                _none_to_empty_dict,
                {
                    vol.Optional(CONF_NAME): cv.string,
                    vol.Optional(CONF_ICON): cv.icon,
                    vol.Optional(CONF_DURATION, default=DEFAULT_DURATION): vol.All(
                        cv.time_period, _format_timedelta
                    ),
                    vol.Optional(
                        CONF_RESTORE, default=DEFAULT_RESTORE
                    ): cv.boolean,
                    vol.Optional(CONF_RESTORE_GRACE_PERIOD, 
                        default=DEFAULT_RESTORE_GRACE_PERIOD): vol.All(
                            cv.time_period, _format_timedelta
                    )
                },
            )
        )
    },
    extra=vol.ALLOW_EXTRA,
)

RELOAD_SERVICE_SCHEMA = vol.Schema({})


async def async_setup(hass: HomeAssistantType, config: ConfigType) -> bool:
    """Set up an input select."""
    component = EntityComponent(_LOGGER, DOMAIN, hass)
    id_manager = collection.IDManager()

    yaml_collection = collection.YamlCollection(
        logging.getLogger(f"{__name__}.yaml_collection"), id_manager
    )
    collection.sync_entity_lifecycle(
        hass, DOMAIN, DOMAIN, component, yaml_collection, Timer.from_yaml
    )

    storage_collection = TimerStorageCollection(
        Store(hass, STORAGE_VERSION, STORAGE_KEY),
        logging.getLogger(f"{__name__}.storage_collection"),
        id_manager,
    )
    collection.sync_entity_lifecycle(
        hass, DOMAIN, DOMAIN, component, storage_collection, Timer
    )

    await yaml_collection.async_load(
        [{CONF_ID: id_, **cfg} for id_, cfg in config.get(DOMAIN, {}).items()]
    )
    await storage_collection.async_load()

    collection.StorageCollectionWebsocket(
        storage_collection, DOMAIN, DOMAIN, CREATE_FIELDS, UPDATE_FIELDS
    ).async_setup(hass)

    async def reload_service_handler(service_call: ServiceCallType) -> None:
        """Reload yaml entities."""
        conf = await component.async_prepare_reload(skip_reset=True)
        if conf is None:
            conf = {DOMAIN: {}}
        await yaml_collection.async_load(
            [{CONF_ID: id_, **cfg} for id_, cfg in conf.get(DOMAIN, {}).items()]
        )

    homeassistant.helpers.service.async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        reload_service_handler,
        schema=RELOAD_SERVICE_SCHEMA,
    )
    component.async_register_entity_service(
        SERVICE_START,
        {vol.Optional(ATTR_DURATION, default=DEFAULT_DURATION): cv.time_period},
        "async_start",
    )
    component.async_register_entity_service(SERVICE_PAUSE, {}, "async_pause")
    component.async_register_entity_service(SERVICE_CANCEL, {}, "async_cancel")
    component.async_register_entity_service(SERVICE_FINISH, {}, "async_finish")

    return True


class TimerStorageCollection(collection.StorageCollection):
    """Timer storage based collection."""

    CREATE_SCHEMA = vol.Schema(CREATE_FIELDS)
    UPDATE_SCHEMA = vol.Schema(UPDATE_FIELDS)

    async def _process_create_data(self, data: Dict) -> Dict:
        """Validate the config is valid."""
        data = self.CREATE_SCHEMA(data)
        # make duration JSON serializeable
        data[CONF_DURATION] = _format_timedelta(data[CONF_DURATION])
        data[CONF_RESTORE] = data[CONF_RESTORE]
        data[CONF_RESTORE_GRACE_PERIOD] = _format_timedelta(data[CONF_RESTORE_GRACE_PERIOD])
        return data

    @callback
    def _get_suggested_id(self, info: Dict) -> str:
        """Suggest an ID based on the config."""
        return info[CONF_NAME]

    async def _update_data(self, data: dict, update_data: Dict) -> Dict:
        """Return a new updated data object."""
        data = {**data, **self.UPDATE_SCHEMA(update_data)}
        # make duration JSON serializeable
        if CONF_DURATION in update_data:
            data[CONF_DURATION] = _format_timedelta(data[CONF_DURATION])
        if CONF_RESTORE in update_data:        
            data[CONF_RESTORE] = str(data[CONF_RESTORE])
        if CONF_RESTORE_GRACE_PERIOD in update_data: 
            data[CONF_RESTORE_GRACE_PERIOD] \
                = _format_timedelta(data[CONF_RESTORE_GRACE_PERIOD])
        return data


class Timer(RestoreEntity):
    """Representation of a timer."""

    def __init__(self, config: Dict):
        """Initialize a timer."""
        self._config: dict = config
        self.editable: bool = True
        self._state: str = STATUS_IDLE
        self._duration = cv.time_period_str(config[CONF_DURATION])
        self._remaining = self._duration
        self._restore = config.get(CONF_RESTORE, DEFAULT_RESTORE)
        if self._restore:
            self._restore_grace_period = cv.time_period_str(
                    config.get(CONF_RESTORE_GRACE_PERIOD, 
                    DEFAULT_RESTORE_GRACE_PERIOD))
        else:
            self._restore_grace_period: Optional[timedelta] = None
        
        self._end: Optional[datetime] = None
        self._listener = None

    @classmethod
    def from_yaml(cls, config: Dict) -> Timer:
        """Return entity instance initialized from yaml storage."""
        timer = cls(config)
        timer.entity_id = ENTITY_ID_FORMAT.format(config[CONF_ID])
        timer.editable = False
        return timer

    @property
    def should_poll(self):
        """If entity should be polled."""
        return False

    @property
    def force_update(self) -> bool:
        """Return True to fix restart issues."""
        return True

    @property
    def name(self):
        """Return name of the timer."""
        return self._config.get(CONF_NAME)

    @property
    def icon(self):
        """Return the icon to be used for this entity."""
        return self._config.get(CONF_ICON)

    @property
    def state(self):
        """Return the current value of the timer."""
        return self._state

    @property
    def state_attributes(self):
        """Return the state attributes."""     
        attrs = {
            ATTR_DURATION: _format_timedelta(self._duration),
            ATTR_EDITABLE: self.editable,
            ATTR_REMAINING: _format_timedelta(self._remaining),
            ATTR_RESTORE: str(self._restore),
        }
        if self._end is not None:
            attrs[ATTR_FINISHES_AT] = str(self._end.replace(tzinfo=timezone.utc).astimezone(tz=None))
        if self._restore:
            attrs[ATTR_RESTORE_GRACE_PERIOD] = _format_timedelta(self._restore_grace_period)

        return attrs

    @property
    def unique_id(self) -> Optional[str]:
        """Return unique id for the entity."""
        return self._config[CONF_ID]

    async def async_added_to_hass(self):
        """Call when entity is about to be added to Home Assistant."""
        if not self._restore:
            self._state = STATUS_IDLE
            return
        
        # Check for previous recorded state
        state = await self.async_get_last_state()
        if state is None:
            self._state = STATUS_IDLE
            return
        else:
            self._restore_state(state.state, state.attributes)
            return
            
        # set state to IDLE if no recorded state, or invalid
        self._state = STATUS_IDLE

    @callback
    def async_start(self, duration: timedelta):
        """Start a timer."""
        if self._listener:
            self._listener()
            self._listener = None
        newduration = None
        if duration:
            newduration = duration

        event = EVENT_TIMER_STARTED
        if self._state == STATUS_ACTIVE or self._state == STATUS_PAUSED:
            event = EVENT_TIMER_RESTARTED

        self._state = STATUS_ACTIVE
        start = dt_util.utcnow().replace(microsecond=0)

        if self._remaining and newduration is None:
            self._end = start + self._remaining
        
        elif newduration:
            self._duration = newduration
            self._remaining = newduration
            self._end = start + self._duration

        else:
            self._remaining = self._duration
            self._end = start + self._duration
            
        self.hass.bus.async_fire(event, {"entity_id": self.entity_id})

        self._listener = async_track_point_in_utc_time(
            self.hass, self._async_finished, self._end
        )
        self.async_write_ha_state()

    @callback
    def async_pause(self):
        """Pause a timer."""
        if self._listener is None:
            return

        self._listener()
        self._listener = None
        self._remaining = self._end - dt_util.utcnow().replace(microsecond=0)
        self._state = STATUS_PAUSED
        self._end = None
        self.hass.bus.async_fire(EVENT_TIMER_PAUSED, {"entity_id": self.entity_id})
        self.async_write_ha_state()

    @callback
    def async_cancel(self):
        """Cancel a timer."""
        if self._listener:
            self._listener()
            self._listener = None
        self._state = STATUS_IDLE
        self._end = None
        self._remaining = timedelta()
        self.hass.bus.async_fire(EVENT_TIMER_CANCELLED, {"entity_id": self.entity_id})
        self.async_write_ha_state()

    @callback
    def async_finish(self):
        """Reset and updates the states, fire finished event."""
        if self._state != STATUS_ACTIVE:
            return

        self._listener = None
        self._state = STATUS_IDLE
        self._end = None
        self._remaining = timedelta()
        self.hass.bus.async_fire(EVENT_TIMER_FINISHED, {"entity_id": self.entity_id})
        self.async_write_ha_state()

    @callback
    def _async_finished(self, time):
        """Reset and updates the states, fire finished event."""
        if self._state != STATUS_ACTIVE:
            return

        self._listener = None
        self._state = STATUS_IDLE
        self._end = None
        self._remaining = timedelta()
        self.hass.bus.async_fire(EVENT_TIMER_FINISHED, {"entity_id": self.entity_id})
        self.async_write_ha_state()

    async def async_update_config(self, config: Dict) -> None:
        """Handle when the config is updated."""
        self._config = config
        self._duration = cv.time_period_str(config[CONF_DURATION])
        self._restore = config.get(CONF_RESTORE, DEFAULT_RESTORE)
        if self._restore:
            self._restore_grace_period: Optional[timedelta] \
                = cv.time_period_str(config.get(CONF_RESTORE_GRACE_PERIOD, 
                    DEFAULT_RESTORE_GRACE_PERIOD))
            self._restore_state(self._state, self.state_attributes)
        else:
            self._listener = None
            self._state = STATUS_IDLE
            self._end = None
            self._remaining = timedelta()
            self._restore_grace_period = timedelta()

        self.async_write_ha_state()
    
    def _restore_state(self, restored_state, state_attributes) -> None:
        if restored_state not in VIABLE_STATUSES:
            self._state = STATUS_IDLE

        self._state = restored_state
        
        # restore last duration if config doesn't have a default
        if not self._config[CONF_DURATION] \
           and not state_attributes.get(ATTR_DURATION) == "None":
            try:
                duration_data = list(map(int, str(state_attributes.get(ATTR_DURATION)).split(":")))
                self._config[CONF_DURATION] = timedelta(hours=duration_data[0],
                                                        minutes=duration_data[1],
                                                        seconds=duration_data[2])
            except ValueError:
                self._config[CONF_DURATION] = timedelta(DEFAULT_DURATION)
            self._duration = cv.time_period_str(config[CONF_DURATION])
        
        # restore remaining (needed for paused state)
        if self._state == STATUS_PAUSED \
           and not state_attributes.get(ATTR_REMAINING) == "None" \
           and not state_attributes.get(ATTR_REMAINING) == str(timedelta()):
            try:
                remaining_dt = list(map(int, str(state_attributes.get(ATTR_REMAINING)).split(":")))
                self._remaining = timedelta(hours=remaining_dt[0],
                                            minutes=remaining_dt[1],
                                            seconds=remaining_dt[2])
            except ValueError:
                self._remaining = self._duration
        else:
            self._remaining = timedelta()
        
        # restore end time
        try:
            if state_attributes.get(ATTR_FINISHES_AT) is not None:
                self._end = datetime.strptime(state_attributes.get(ATTR_FINISHES_AT), "%Y-%m-%d %H:%M:%S%z")
            else:
                self._end = None
        except ValueError:
            self._end = None
        
        # timer was active
        if self._state == STATUS_ACTIVE:
            try:
                # account for lost time
                if self._end:
                    self._remaining = self._end - dt_util.utcnow().replace(microsecond=0)
                else:
                    self._remaining = timedelta()
                _LOGGER.debug("%s : Restored remaining: %s",self._config.get(CONF_NAME),_format_timedelta(self._remaining))
                
                # only restore if restore_grace_period not exceeded
                if self._remaining + self._restore_grace_period >= timedelta():
                    self._state = STATUS_PAUSED
                    self._end = None
                    self.async_start(None)
                else:
                    self._state = STATUS_IDLE
            except ValueError:
                self._remaining = timedelta()
                self._end = None
                self._state = STATUS_IDLE

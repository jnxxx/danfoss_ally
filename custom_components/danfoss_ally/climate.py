"""Support for Danfoss Ally thermostats."""
import logging

import voluptuous as vol
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    HVAC_MODE_AUTO,
    HVAC_MODE_HEAT,
    PRESET_AWAY,
    PRESET_HOME,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
    ATTR_PRESET_MODE,
    ATTR_HVAC_MODE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, TEMP_CELSIUS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import entity_platform
import functools as ft

from . import AllyConnector
from .const import (
    DATA,
    DOMAIN,
    HVAC_MODE_MANUAL,
    PRESET_MANUAL,
    PRESET_PAUSE,
    PRESET_HOLIDAY,
    SIGNAL_ALLY_UPDATE_RECEIVED,
)
from .entity import AllyDeviceEntity

# Custom preset for pause mode


_LOGGER = logging.getLogger(__name__)


class AllyClimate(AllyDeviceEntity, ClimateEntity):
    """Representation of a Danfoss Ally climate entity."""

    def __init__(
        self,
        ally,
        name,
        device_id,
        model,
        heat_min_temp,
        heat_max_temp,
        heat_step,
        supported_hvac_modes,
        support_flags,
    ):
        """Initialize Danfoss Ally climate entity."""
        self._ally = ally
        self._device = ally.devices[device_id]
        self._device_id = device_id
        super().__init__(name, device_id, "climate", model)

        _LOGGER.debug("Device_id: %s --- Device: %s", self._device_id, self._device)

        self._unique_id = f"climate_{device_id}_ally"

        self._supported_hvac_modes = supported_hvac_modes
        self._supported_preset_modes = [
            PRESET_HOME,
            PRESET_AWAY,
            PRESET_PAUSE,
            PRESET_MANUAL,
            PRESET_HOLIDAY
        ]
        self._support_flags = support_flags

        self._available = False

        # Current temperature
        if "temperature" in self._device:
            self._cur_temp = self._device["temperature"]
        else:
            # TEMPORARY fix for missing temperature sensor
            self._cur_temp = self.get_setpoint_for_current_mode() #self._device["setpoint"]

        # Low temperature set in Ally app
        if "lower_temp" in self._device:
            self._heat_min_temp = self._device["lower_temp"]
        else:
            self._heat_min_temp = heat_min_temp

        # High temperature set in Ally app
        if "upper_temp" in self._device:
            self._heat_max_temp = self._device["upper_temp"]
        else:
            self._heat_max_temp = heat_max_temp

        self._heat_step = heat_step
        self._target_temp = None

    async def async_added_to_hass(self):
        """Register for sensor updates."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ALLY_UPDATE_RECEIVED,
                self._async_update_callback,
            )
        )

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._unique_id

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        # return self._cur_temp
        if "temperature" in self._device:
            return self._device["temperature"]
        else:
            # TEMPORARY fix for missing temperature sensor
            return self.get_setpoint_for_current_mode() #self._device["setpoint"]

    @property
    def hvac_mode(self):
        """Return hvac operation ie. heat, cool mode.
        Need to be one of HVAC_MODE_*.
        """
        if "mode" in self._device:
            if (
                self._device["mode"] == "at_home"
                or self._device["mode"] == "leaving_home"
            ):
                return HVAC_MODE_AUTO
            elif (self._device["mode"] == "manual" or self._device["mode"] == "pause" or self._device["mode"] == "holiday"):
                return HVAC_MODE_HEAT

    @property
    def preset_mode(self):
        """The current active preset."""
        if "mode" in self._device:
            if self._device["mode"] == "at_home":
                return PRESET_HOME
            elif self._device["mode"] == "leaving_home":
                return PRESET_AWAY
            elif self._device["mode"] == "pause":
                return PRESET_PAUSE
            elif self._device["mode"] == "manual":
                return PRESET_MANUAL
            elif self._device["mode"] == "holiday":
                return PRESET_HOLIDAY

    @property
    def hvac_modes(self):
        """Return the list of available hvac operation modes.
        Need to be a subset of HVAC_MODES.
        """
        return self._supported_hvac_modes

    @property
    def preset_modes(self):
        """Return the list of available preset modes."""
        return self._supported_preset_modes

    def set_preset_mode(self, preset_mode):
        """Set new target preset mode."""

        _LOGGER.debug("Setting preset mode to %s", preset_mode)

        if preset_mode == PRESET_HOME:
            mode = "at_home"
        elif preset_mode == PRESET_AWAY:
            mode = "leaving_home"
        elif preset_mode == PRESET_PAUSE:
            mode = "pause"
        elif preset_mode == PRESET_MANUAL:
            mode = "manual"
        elif preset_mode == PRESET_HOLIDAY:
            mode = "holiday"

        if mode is None:
            return

        self._device["mode"] = mode     # Update current copy of device data
        self._ally.set_mode(self._device_id, mode)

        # Update UI
        self.async_write_ha_state()

    @property
    def hvac_action(self):
        """Return the current running hvac operation if supported.
        Need to be one of CURRENT_HVAC_*.
        """
        if "work_state" in self._device:
            if self._device["work_state"] == "Heat":
                return CURRENT_HVAC_HEAT
            elif self._device["work_state"] == "NoHeat":
                return CURRENT_HVAC_IDLE

    @property
    def temperature_unit(self):
        """Return the unit of measurement used by the platform."""
        return TEMP_CELSIUS

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._heat_step

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self.get_setpoint_for_current_mode() #self._device["setpoint"]

    def set_temperature(self, **kwargs):
        """Set new target temperature."""

        if ATTR_TEMPERATURE in kwargs:
            temperature = kwargs.get(ATTR_TEMPERATURE)

        if ATTR_PRESET_MODE in kwargs:
            setpoint_code = self.get_setpoint_code_for_mode(kwargs.get(ATTR_PRESET_MODE))   # Preset_mode sent from action
        elif ATTR_HVAC_MODE in kwargs:
            value = kwargs.get(ATTR_HVAC_MODE)                                              # HVAC_mode sent from action
            if value == HVAC_MODE_AUTO:
                setpoint_code = self.get_setpoint_code_for_mode("at_home")
            if value == HVAC_MODE_HEAT:
                setpoint_code = self.get_setpoint_code_for_mode("manual")
        else:
            setpoint_code = self.get_setpoint_code_for_mode(self._device["mode"])           # Current preset_mode
        #_LOGGER.debug("setpoint_code: %s", setpoint_code)

        changed = False
        if temperature is not None and setpoint_code is not None:
            self._device[setpoint_code] = temperature # Update temperature in current copy
            self._ally.set_temperature(self._device_id, temperature, setpoint_code)
            changed = True

        # Update UI
        if changed:
            self.async_write_ha_state()

    async def set_preset_temperature(self, **kwargs):
        await self.hass.async_add_executor_job(
            ft.partial(self.set_temperature, **kwargs)
        )


    @property
    def available(self):
        """Return if the device is available."""
        return self._device["online"]

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._heat_min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._heat_max_temp

    @callback
    def _async_update_data(self):
        """Load data."""
        _LOGGER.debug("Loading new climate data for device %s", self._device_id)
        self._device = self._ally.devices[self._device_id]

    @callback
    def _async_update_callback(self):
        """Load data and update state."""
        self._async_update_data()
        self.async_write_ha_state()

    def set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""

        _LOGGER.debug("Setting hvac mode to %s", hvac_mode)

        if hvac_mode == HVAC_MODE_AUTO:
            mode = "at_home"  # We have to choose either at_home or leaving_home
        elif hvac_mode == HVAC_MODE_HEAT:
            mode = "manual"

        if mode is None:
            return

        self._device["mode"] = mode     # Update current copy of device data
        self._ally.set_mode(self._device_id, mode)

        # Update UI
        self.async_write_ha_state()


    def get_setpoint_code_for_mode(self, mode, for_writing = True):
        setpoint_code = None
        if for_writing == False and "banner_ctrl" in self._device and bool(self._device['banner_ctrl']):
            # Temperature setpoint is overridden locally at the thermostate
            setpoint_code = "manual_mode_fast"
        elif mode == "at_home" or mode == "home":
            setpoint_code = "at_home_setting"
        elif mode == "leaving_home" or mode == "away":
            setpoint_code = "leaving_home_setting"
        elif mode == "pause":
            setpoint_code = "pause_setting"
        elif mode == "manual":
            setpoint_code = "manual_mode_fast"
        elif mode == "holiday":
            setpoint_code = "holiday_setting"
        return setpoint_code

    def get_setpoint_for_current_mode(self):
        setpoint = None
        if "mode" in self._device:
            setpoint_code = self.get_setpoint_code_for_mode(self._device["mode"], False)

            if setpoint_code is not None and setpoint_code in self._device:
                setpoint = self._device[setpoint_code]

        return(setpoint)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up the Danfoss Ally climate platform."""

    platform = entity_platform.current_platform.get()
    platform.async_register_entity_service(
        "set_preset_temperature",
        {
            vol.Required("temperature"): vol.Coerce(float),
            vol.Optional("preset_mode"): str,
        },
        "set_preset_temperature"
    )

    ally: AllyConnector = hass.data[DOMAIN][entry.entry_id][DATA]
    entities = await hass.async_add_executor_job(_generate_entities, ally)
    #_LOGGER.debug(ally.devices)
    if entities:
        async_add_entities(entities, True)


def _generate_entities(ally: AllyConnector):
    """Create all climate entities."""
    _LOGGER.debug("Setting up Danfoss Ally climate entities")
    entities = []
    for device in ally.devices:
        if ally.devices[device]["isThermostat"]:
            _LOGGER.debug("Found climate entity for %s", ally.devices[device]["name"])
            entity = create_climate_entity(ally, ally.devices[device]["name"], device, ally.devices[device]["model"])
            if entity:
                entities.append(entity)
    return entities


def create_climate_entity(ally, name: str, device_id: str, model: str) -> AllyClimate:
    """Create a Danfoss Ally climate entity."""

    support_flags = SUPPORT_TARGET_TEMPERATURE | SUPPORT_PRESET_MODE
    supported_hvac_modes = [HVAC_MODE_AUTO, HVAC_MODE_HEAT]
    heat_min_temp = 4.5
    heat_max_temp = 35.0
    heat_step = 0.5

    entity = AllyClimate(
        ally,
        name,
        device_id,
        model,
        heat_min_temp,
        heat_max_temp,
        heat_step,
        supported_hvac_modes,
        support_flags,
    )
    return entity

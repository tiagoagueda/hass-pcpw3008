"""ProfiCare PC-PW 3008 BT body scale."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_AGE,
    CONF_HEIGHT,
    CONF_MALE,
    CONF_SLOT,
    DEFAULT_AGE,
    DEFAULT_HEIGHT,
    DEFAULT_SLOT,
    DOMAIN,
)
from .coordinator import Profile, ScaleCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type ScaleConfigEntry = ConfigEntry[ScaleCoordinator]


def _profile_from_entry(entry: ConfigEntry) -> Profile:
    """Profile lives in options so it can be edited without re-pairing."""
    source = {**entry.data, **entry.options}
    return Profile(
        male=bool(source.get(CONF_MALE, True)),
        age=int(source.get(CONF_AGE, DEFAULT_AGE)),
        height_cm=int(source.get(CONF_HEIGHT, DEFAULT_HEIGHT)),
        slot=int(source.get(CONF_SLOT, DEFAULT_SLOT)),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ScaleConfigEntry) -> bool:
    """Set up the scale from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    coordinator = ScaleCoordinator(hass, address, _profile_from_entry(entry))
    await coordinator.async_start()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ScaleConfigEntry) -> bool:
    """Tear down the config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload_on_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A changed profile changes what the scale computes, so reconnect with it."""
    await hass.config_entries.async_reload(entry.entry_id)

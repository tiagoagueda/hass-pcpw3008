"""ProfiCare PC-PW 3008 BT body scale."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_AGE,
    CONF_EXPECTED_WEIGHT,
    CONF_HEIGHT,
    CONF_MALE,
    CONF_NAME,
    CONF_SLOT,
    CONF_USER_ID,
    DEFAULT_AGE,
    DEFAULT_HEIGHT,
    DEFAULT_SLOT,
    DOMAIN,
    LOCAL_NAME,
    SUBENTRY_PERSON,
)
from .coordinator import ScaleCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

SERVICE_REASSIGN = "reassign_measurement"
ATTR_PERSON = "person"

type ScaleConfigEntry = ConfigEntry[ScaleCoordinator]


def _migrate_legacy_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, subentry_id: str, address: str
) -> None:
    """Re-point pre-multi-user entities at the person they became.

    Before multi-user, unique ids were "<address>_<key>"; now they are
    "<address>_<subentry>_<key>". Without this the old entities are stranded as
    permanently unavailable and the new ones get "_2" appended to their entity
    ids — losing the history that was already recorded under the old name.
    """
    registry = er.async_get(hass)
    taken = {
        e.unique_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    prefix = f"{address}_"
    new_prefix = f"{address}_{subentry_id}_"

    @callback
    def _update(entry_: er.RegistryEntry) -> dict[str, str] | None:
        uid = entry_.unique_id
        if not uid.startswith(prefix) or uid.startswith(new_prefix):
            return None
        new_uid = f"{new_prefix}{uid[len(prefix):]}"
        # A collision means the new entity already exists; leave both alone
        # rather than crash the whole migration.
        if new_uid in taken:
            return None
        return {"new_unique_id": new_uid}

    er.async_migrate_entries(hass, entry.entry_id, _update)


def _ensure_person_subentry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Carry a pre-multi-user config entry over to a person subentry.

    Early versions stored one profile directly on the config entry. Rather than
    make those users re-pair, promote that profile to the first person so their
    existing history keeps flowing.
    """
    if any(s.subentry_type == SUBENTRY_PERSON for s in entry.subentries.values()):
        return

    source = {**entry.data, **entry.options}
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={
                CONF_NAME: LOCAL_NAME,
                CONF_USER_ID: None,
                CONF_MALE: bool(source.get(CONF_MALE, True)),
                CONF_AGE: int(source.get(CONF_AGE, DEFAULT_AGE)),
                CONF_HEIGHT: int(source.get(CONF_HEIGHT, DEFAULT_HEIGHT)),
                CONF_SLOT: int(source.get(CONF_SLOT, DEFAULT_SLOT)),
                CONF_EXPECTED_WEIGHT: None,
            },
            subentry_type=SUBENTRY_PERSON,
            title=LOCAL_NAME,
            unique_id=None,
        ),
    )

    created = next(
        s_ for s_ in entry.subentries.values() if s_.subentry_type == SUBENTRY_PERSON
    )
    _migrate_legacy_unique_ids(hass, entry, created.subentry_id, entry.data[CONF_ADDRESS])


async def async_setup_entry(hass: HomeAssistant, entry: ScaleConfigEntry) -> bool:
    """Set up the scale from a config entry."""
    _ensure_person_subentry(hass, entry)

    coordinator = ScaleCoordinator(hass, entry, entry.data[CONF_ADDRESS])
    await coordinator.async_start()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ScaleConfigEntry) -> bool:
    """Tear down the config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A changed profile changes what the scale computes, so reconnect with it."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the reassignment service once."""
    if hass.services.has_service(DOMAIN, SERVICE_REASSIGN):
        return

    async def _reassign(call: ServiceCall) -> None:
        """Move the last weigh-in to a different person.

        Weight and a recomputed BMI move across; the scale's composition figures
        do not, because they were computed for the original profile's body and
        cannot be redone for another.
        """
        target = call.data[ATTR_PERSON]
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator: ScaleCoordinator | None = getattr(entry, "runtime_data", None)
            if coordinator is None:
                continue
            for person in coordinator.people:
                if target in (person.subentry_id, person.name):
                    if coordinator.reassign_last(person.subentry_id):
                        return
        raise vol.Invalid(f"No measurement could be reassigned to {target!r}")

    hass.services.async_register(
        DOMAIN,
        SERVICE_REASSIGN,
        _reassign,
        schema=vol.Schema({vol.Required(ATTR_PERSON): cv.string}),
    )

"""Sensors for the ProfiCare PC-PW 3008 BT scale — one device per person."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CONF_ADDRESS, UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ScaleConfigEntry
from .const import DOMAIN, SUBENTRY_PERSON
from .coordinator import ScaleCoordinator
from .protocol import FinalFrame


@dataclass(frozen=True, kw_only=True)
class ScaleSensorDescription(SensorEntityDescription):
    """Describes one value pulled out of a settled measurement."""

    value_fn: Callable[[FinalFrame], float | int | None]


SENSORS: tuple[ScaleSensorDescription, ...] = (
    ScaleSensorDescription(
        key="weight",
        icon="mdi:scale-bathroom",
        translation_key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.weight_kg,
    ),
    ScaleSensorDescription(
        key="fat",
        icon="mdi:percent",
        translation_key="fat",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.fat_pct,
    ),
    ScaleSensorDescription(
        key="water",
        icon="mdi:water-percent",
        translation_key="water",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.water_pct,
    ),
    ScaleSensorDescription(
        key="muscle",
        icon="mdi:arm-flex",
        translation_key="muscle",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.muscle_pct,
    ),
    ScaleSensorDescription(
        key="bone",
        icon="mdi:bone",
        translation_key="bone",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.bone_kg,
    ),
    ScaleSensorDescription(
        key="bmr",
        icon="mdi:fire",
        translation_key="bmr",
        native_unit_of_measurement="kcal",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.bmr_kcal,
    ),
    ScaleSensorDescription(
        key="bmi",
        icon="mdi:human-male-height",
        translation_key="bmi",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.bmi,
    ),
    ScaleSensorDescription(
        key="visceral_fat",
        icon="mdi:stomach",
        translation_key="visceral_fat",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.visceral_fat,
    ),
    ScaleSensorDescription(
        key="body_age",
        icon="mdi:cake-variant",
        translation_key="body_age",
        native_unit_of_measurement="a",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.body_age,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ScaleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one device worth of sensors per configured person."""
    coordinator = entry.runtime_data
    address = entry.data[CONF_ADDRESS]

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_PERSON:
            continue
        async_add_entities(
            [
                ScaleSensor(coordinator, description, address, subentry.subentry_id,
                            subentry.title)
                for description in SENSORS
            ],
            config_subentry_id=subentry.subentry_id,
        )


class ScaleSensor(CoordinatorEntity[ScaleCoordinator], SensorEntity):
    """One field of one person's most recent measurement."""

    _attr_has_entity_name = True
    entity_description: ScaleSensorDescription

    def __init__(
        self,
        coordinator: ScaleCoordinator,
        description: ScaleSensorDescription,
        address: str,
        subentry_id: str,
        person_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._subentry_id = subentry_id
        self._attr_unique_id = f"{address}_{subentry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{address}_{subentry_id}")},
            manufacturer="ProfiCare",
            model="PC-PW 3008 BT",
            name=person_name,
            via_device=(DOMAIN, address),
        )

    @property
    def _measurement(self) -> FinalFrame | None:
        return (self.coordinator.data or {}).get(self._subentry_id)

    @property
    def available(self) -> bool:
        """Available once this person has a measurement.

        The scale is powered down almost all the time, so tying availability to
        the radio would leave every sensor unavailable between weigh-ins and
        break history. The last reading stays valid until the next one.
        """
        return self._measurement is not None

    @property
    def native_value(self) -> float | int | None:
        measurement = self._measurement
        if measurement is None:
            return None
        return self.entity_description.value_fn(measurement)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose how confident the attribution was, on the weight sensor.

        Without this an ambiguous match looks identical to a certain one, and
        the user has no signal that a reading may belong to somebody else.
        """
        if self.entity_description.key != "weight":
            return None
        attribution = self.coordinator.last_attribution
        if attribution is None or attribution.person.subentry_id != self._subentry_id:
            return None
        return {
            "profile_used": (
                attribution.profile_pushed_for.name
                if attribution.profile_pushed_for
                else None
            ),
            "body_composition_valid": attribution.exact,
            "match_margin_kg": attribution.margin_kg,
        }

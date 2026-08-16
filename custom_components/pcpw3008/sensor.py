"""Sensors for the ProfiCare PC-PW 3008 BT scale."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CONF_ADDRESS, UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ScaleConfigEntry
from .const import DOMAIN, LOCAL_NAME
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
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the scale's sensors."""
    coordinator = entry.runtime_data
    address = entry.data[CONF_ADDRESS]
    async_add_entities(
        ScaleSensor(coordinator, description, address) for description in SENSORS
    )


class ScaleSensor(CoordinatorEntity[ScaleCoordinator], SensorEntity):
    """One field of the most recent settled measurement."""

    _attr_has_entity_name = True
    entity_description: ScaleSensorDescription

    def __init__(
        self,
        coordinator: ScaleCoordinator,
        description: ScaleSensorDescription,
        address: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="ProfiCare",
            model="PC-PW 3008 BT",
            name=LOCAL_NAME,
        )

    @property
    def available(self) -> bool:
        """Available once a measurement exists.

        The scale is powered down almost all the time, so tying availability to
        the radio would leave every sensor unavailable between weigh-ins and
        break history. The last reading stays valid until the next one.
        """
        return self.coordinator.data is not None

    @property
    def native_value(self) -> float | int | None:
        measurement = self.coordinator.data
        if measurement is None:
            return None
        return self.entity_description.value_fn(measurement)

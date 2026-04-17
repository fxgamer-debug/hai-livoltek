"""Sensor platform for the Livoltek integration."""
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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfMass,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    COORDINATOR_FAST,
    COORDINATOR_MEDIUM,
    COORDINATOR_WEEKLY,
    DOMAIN,
)
from .entity import LivoltekEntity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    """Best-effort conversion of API string/numeric values to float."""
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    """Best-effort int conversion."""
    f = _to_float(value)
    if f is None:
        return None
    return int(f)


def _point(d: dict[str, Any], key: str) -> Any:
    """Extract `.value` from a `point/info` register dict."""
    point = d.get(key)
    if isinstance(point, dict):
        return point.get("value")
    return point


@dataclass(frozen=True, kw_only=True)
class LivoltekSensorEntityDescription(SensorEntityDescription):
    """Describes a Livoltek sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


# ---------------------------------------------------------------------------
# FAST coordinator sensors — energyStorageInfo
# ---------------------------------------------------------------------------


FAST_SENSORS: tuple[LivoltekSensorEntityDescription, ...] = (
    # ----- PV -----
    LivoltekSensorEntityDescription(
        key="pv_power",
        translation_key="pv_power",
        name="PV power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("pvPower")),
    ),
    LivoltekSensorEntityDescription(
        key="pv_string_1_power",
        translation_key="pv_string_1_power",
        name="PV string 1 power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("p1Power")),
    ),
    LivoltekSensorEntityDescription(
        key="pv_string_2_power",
        translation_key="pv_string_2_power",
        name="PV string 2 power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("p2Power")),
    ),
    LivoltekSensorEntityDescription(
        key="pv_string_1_voltage",
        translation_key="pv_string_1_voltage",
        name="PV string 1 voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("p1Voltage")),
    ),
    LivoltekSensorEntityDescription(
        key="pv_string_2_voltage",
        translation_key="pv_string_2_voltage",
        name="PV string 2 voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("p2Voltage")),
    ),
    # ----- Grid (note: API field is misspelled `gird*`) -----
    LivoltekSensorEntityDescription(
        key="grid_power",
        translation_key="grid_power",
        name="Grid power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # Negative = exporting, positive = importing.
        value_fn=lambda d: _to_float(d.get("girdPower")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        name="Grid voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("girdVoltage")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        name="Grid frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("girdFrequency")),
    ),
    # ----- Load -----
    LivoltekSensorEntityDescription(
        key="load_power",
        translation_key="load_power",
        name="Load power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("loadActivePower")),
    ),
    LivoltekSensorEntityDescription(
        key="load_voltage",
        translation_key="load_voltage",
        name="Load voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("loadVoltage")),
    ),
    # ----- Battery -----
    LivoltekSensorEntityDescription(
        key="battery_power",
        translation_key="battery_power",
        name="Battery power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # Negative = discharging, positive = charging.
        value_fn=lambda d: _to_float(d.get("batteryActivePower")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        name="Battery voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("batteryVoltage")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_current",
        translation_key="battery_current",
        name="Battery current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("batteryCurrent")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_soc",
        translation_key="battery_soc",
        name="Battery SOC",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _to_float(d.get("batteryRestSoc")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_max_temp",
        translation_key="battery_max_temp",
        name="Battery max temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("batteryMaxTemperature")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_min_temp",
        translation_key="battery_min_temp",
        name="Battery min temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("batteryMinTemperature")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_cell_voltage_max",
        translation_key="battery_cell_voltage_max",
        name="Battery cell voltage max",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: _to_float(d.get("vCellMax")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_cell_voltage_min",
        translation_key="battery_cell_voltage_min",
        name="Battery cell voltage min",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: _to_float(d.get("vCellMin")),
    ),
    # ----- Inverter -----
    LivoltekSensorEntityDescription(
        key="inverter_temperature",
        translation_key="inverter_temperature",
        name="Inverter temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("temperature")),
    ),
    # ----- EPS / AC -----
    LivoltekSensorEntityDescription(
        key="eps_power",
        translation_key="eps_power",
        name="EPS power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("epsPower")),
    ),
    LivoltekSensorEntityDescription(
        key="eps_voltage",
        translation_key="eps_voltage",
        name="EPS voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(d.get("epsVoltage")),
    ),
    LivoltekSensorEntityDescription(
        key="ac_power",
        translation_key="ac_power",
        name="AC output power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("activePower")),
    ),
    # ----- Energy totals -----
    LivoltekSensorEntityDescription(
        key="pv_energy_today",
        translation_key="pv_energy_today",
        name="PV energy today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("pvFieldToday")),
    ),
    LivoltekSensorEntityDescription(
        key="pv_energy_month",
        translation_key="pv_energy_month",
        name="PV energy this month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("pvFieldMonth")),
    ),
    LivoltekSensorEntityDescription(
        key="pv_energy_total",
        translation_key="pv_energy_total",
        name="PV energy total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("pvFieldTotal")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_export_today",
        translation_key="grid_export_today",
        name="Grid export today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("girdExportedToday")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_export_month",
        translation_key="grid_export_month",
        name="Grid export this month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("girdExportedMonth")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_export_total",
        translation_key="grid_export_total",
        name="Grid export total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("girdExportedTotal")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_import_today",
        translation_key="grid_import_today",
        name="Grid import today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("girdImportedToday")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_import_month",
        translation_key="grid_import_month",
        name="Grid import this month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("girdImportedMonth")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_import_total",
        translation_key="grid_import_total",
        name="Grid import total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("girdImportedTotal")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_charged_today",
        translation_key="battery_charged_today",
        name="Battery charged today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("batteryCDToday")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_charged_month",
        translation_key="battery_charged_month",
        name="Battery charged this month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("batteryCDMonth")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_charged_total",
        translation_key="battery_charged_total",
        name="Battery charged total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("batteryCDTotal")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_discharged_today",
        translation_key="battery_discharged_today",
        name="Battery discharged today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("batteryFDToday")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_discharged_month",
        translation_key="battery_discharged_month",
        name="Battery discharged this month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("batteryFDMonth")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_discharged_total",
        translation_key="battery_discharged_total",
        name="Battery discharged total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("batteryFDTotal")),
    ),
    LivoltekSensorEntityDescription(
        key="load_consumption_today",
        translation_key="load_consumption_today",
        name="Load consumption today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("loadConsumptionToday")),
    ),
    LivoltekSensorEntityDescription(
        key="load_consumption_month",
        translation_key="load_consumption_month",
        name="Load consumption this month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("loadConsumptionMonth")),
    ),
    LivoltekSensorEntityDescription(
        key="load_consumption_total",
        translation_key="load_consumption_total",
        name="Load consumption total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("loadConsumptionTotal")),
    ),
    LivoltekSensorEntityDescription(
        key="eps_energy_total",
        translation_key="eps_energy_total",
        name="EPS energy total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(d.get("epsConsumptionTotal")),
    ),
    # ----- Diagnostic / static (disabled by default) -----
    LivoltekSensorEntityDescription(
        key="battery_sn",
        translation_key="battery_sn",
        name="Battery serial number",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("battery1Sn"),
    ),
    LivoltekSensorEntityDescription(
        key="battery_capacity_kwh",
        translation_key="battery_capacity_kwh",
        name="Battery capacity",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _to_float(d.get("batteryCapacityKwh")),
    ),
    LivoltekSensorEntityDescription(
        key="arm_firmware",
        translation_key="arm_firmware",
        name="ARM firmware version",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("armVersion"),
    ),
    LivoltekSensorEntityDescription(
        key="dsp_firmware",
        translation_key="dsp_firmware",
        name="DSP firmware version",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("masterDSPVersion"),
    ),
    LivoltekSensorEntityDescription(
        key="bms_firmware",
        translation_key="bms_firmware",
        name="BMS firmware version",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("bMSVersion"),
    ),
)


# ---------------------------------------------------------------------------
# MEDIUM coordinator sensors — signal + power_flow + alarms
# ---------------------------------------------------------------------------


def _signal(d: dict[str, Any]) -> dict[str, Any]:
    return d.get("signal") or {}


def _power_flow(d: dict[str, Any]) -> dict[str, Any]:
    return d.get("power_flow") or {}


def _alarms(d: dict[str, Any]) -> list[dict[str, Any]]:
    return d.get("alarms") or []


MEDIUM_SENSORS: tuple[LivoltekSensorEntityDescription, ...] = (
    LivoltekSensorEntityDescription(
        key="pcs_status",
        translation_key="pcs_status",
        name="PCS status",
        # Values are documented in translations/en.json under "pcsStatus".
        device_class=SensorDeviceClass.ENUM,
        options=["normal", "standby", "generating", "offline", "self_check", "upgrading"],
        value_fn=lambda d: _PCS_STATUS_MAP.get(_to_int(_signal(d).get("pcsStatus"))),
    ),
    LivoltekSensorEntityDescription(
        key="work_status",
        translation_key="work_status",
        name="Work status",
        value_fn=lambda d: _signal(d).get("workStatus"),
    ),
    LivoltekSensorEntityDescription(
        key="smart_meter_power",
        translation_key="smart_meter_power",
        name="Smart meter power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _to_float(_power_flow(d).get("smActivePower")),
    ),
    LivoltekSensorEntityDescription(
        key="co2_saved",
        translation_key="co2_saved",
        name="CO2 saved",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.WEIGHT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _to_float(_signal(d).get("carbonReduction")),
    ),
    LivoltekSensorEntityDescription(
        key="generator_state",
        translation_key="generator_state",
        name="Generator state",
        entity_registry_enabled_default=False,
        value_fn=lambda d: _power_flow(d).get("generatorState"),
    ),
    # Alarm count sensors. ``actionId == 0`` means the alarm is still active.
    LivoltekSensorEntityDescription(
        key="alarm_count_active",
        translation_key="alarm_count_active",
        name="Active alarm count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(
            1
            for a in _alarms(d)
            if a.get("actionId") == 0 and (a.get("level") or 1) >= 2
        ),
    ),
    LivoltekSensorEntityDescription(
        key="alarm_count_secondary",
        translation_key="alarm_count_secondary",
        name="Secondary alarm count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(
            1 for a in _alarms(d) if a.get("level") == 2 and a.get("actionId") == 0
        ),
    ),
    LivoltekSensorEntityDescription(
        key="alarm_count_important",
        translation_key="alarm_count_important",
        name="Important alarm count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(
            1 for a in _alarms(d) if a.get("level") == 3 and a.get("actionId") == 0
        ),
    ),
    LivoltekSensorEntityDescription(
        key="alarm_count_urgent",
        translation_key="alarm_count_urgent",
        name="Urgent alarm count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(
            1 for a in _alarms(d) if a.get("level") == 4 and a.get("actionId") == 0
        ),
    ),
    LivoltekSensorEntityDescription(
        key="last_alarm_code",
        translation_key="last_alarm_code",
        name="Last alarm code",
        value_fn=lambda d: (_alarms(d)[0].get("alarmCode") if _alarms(d) else None),
    ),
    LivoltekSensorEntityDescription(
        key="last_alarm_description",
        translation_key="last_alarm_description",
        name="Last alarm description",
        value_fn=lambda d: (_alarms(d)[0].get("content") if _alarms(d) else None),
    ),
)


_PCS_STATUS_MAP: dict[int | None, str | None] = {
    0: "normal",
    1: "standby",
    2: "generating",
    3: "offline",
    4: "self_check",
    5: "upgrading",
}


# ---------------------------------------------------------------------------
# WEEKLY coordinator sensors — point/info filtered subset
# ---------------------------------------------------------------------------


_WORK_MODE_MAP = {
    "0": "self_use",
    "1": "back_up",
    "2": "feed_in_first",
    0: "self_use",
    1: "back_up",
    2: "feed_in_first",
}


WEEKLY_SENSORS: tuple[LivoltekSensorEntityDescription, ...] = (
    LivoltekSensorEntityDescription(
        key="work_mode",
        translation_key="work_mode",
        name="Work mode",
        device_class=SensorDeviceClass.ENUM,
        options=["self_use", "back_up", "feed_in_first"],
        value_fn=lambda d: _WORK_MODE_MAP.get(_point(d, "workModel")),
    ),
    LivoltekSensorEntityDescription(
        key="discharge_end_soc",
        translation_key="discharge_end_soc",
        name="Discharge end SOC (grid)",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda d: _to_int(_point(d, "dischargeEndSOC")),
    ),
    LivoltekSensorEntityDescription(
        key="discharge_end_soc_eps",
        translation_key="discharge_end_soc_eps",
        name="Discharge end SOC (EPS)",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda d: _to_int(_point(d, "dischargeEndSOCEps")),
    ),
    LivoltekSensorEntityDescription(
        key="max_charge_current",
        translation_key="max_charge_current",
        name="Max charge current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(_point(d, "chargingCurrent")),
    ),
    LivoltekSensorEntityDescription(
        key="max_discharge_current",
        translation_key="max_discharge_current",
        name="Max discharge current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda d: _to_float(_point(d, "dischargingCurrent")),
    ),
    LivoltekSensorEntityDescription(
        key="battery_soh",
        translation_key="battery_soh",
        name="Battery SOH",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda d: _to_float(_point(d, "BMSSOH")),
    ),
    LivoltekSensorEntityDescription(
        key="warning_soc",
        translation_key="warning_soc",
        name="Warning SOC",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda d: _to_int(_point(d, "WarningSoc")),
    ),
    LivoltekSensorEntityDescription(
        key="grid_feed_power_limit",
        translation_key="grid_feed_power_limit",
        name="Grid feed power limit",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: _to_float(_point(d, "gridFeedPowerLimit")),
    ),
)


# ---------------------------------------------------------------------------
# Entity + setup
# ---------------------------------------------------------------------------


class LivoltekSensor(LivoltekEntity, SensorEntity):
    """Generic Livoltek sensor backed by a value_fn."""

    entity_description: LivoltekSensorEntityDescription

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        try:
            return self.entity_description.value_fn(data)
        except Exception:  # noqa: BLE001 — never crash a sensor read
            return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register Livoltek sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    fast = data[COORDINATOR_FAST]
    medium = data[COORDINATOR_MEDIUM]
    weekly = data[COORDINATOR_WEEKLY]

    entities: list[LivoltekSensor] = []
    entities.extend(LivoltekSensor(fast, entry, desc) for desc in FAST_SENSORS)
    entities.extend(LivoltekSensor(medium, entry, desc) for desc in MEDIUM_SENSORS)
    entities.extend(LivoltekSensor(weekly, entry, desc) for desc in WEEKLY_SENSORS)

    async_add_entities(entities)

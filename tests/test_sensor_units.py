"""Regression: HA need not initialize _attr_native_unit_of_measurement."""
import importlib
import sys
import types
import unittest
from unittest.mock import patch
from datetime import date, datetime, timezone
from pathlib import Path


class SensorUnitTests(unittest.TestCase):
    def test_all_account_units_and_forecast_date(self):
        def module(name, **attrs):
            result = types.ModuleType(name)
            result.__dict__.update(attrs)
            return result
        class SensorEntity:
            # Deliberately no unit attribute: this reproduces the failing HA path.
            pass
        class CoordinatorEntity:
            def __init__(self, coordinator):
                self.coordinator = coordinator
        package = module("vdsina_unit_test")
        package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/vdsina")]
        modules = {
            "vdsina_unit_test": package,
            "homeassistant": module("homeassistant"),
            "homeassistant.components": module("homeassistant.components"),
            "homeassistant.components.sensor": module("homeassistant.components.sensor",
                SensorEntity=SensorEntity, SensorDeviceClass=types.SimpleNamespace(DATE="date", TIMESTAMP="timestamp", DATA_SIZE="data_size"),
                SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement")),
            "homeassistant.core": module("homeassistant.core", callback=lambda f: f),
            "homeassistant.helpers": module("homeassistant.helpers"),
            "homeassistant.helpers.entity": module("homeassistant.helpers.entity", DeviceInfo=dict,
                EntityCategory=types.SimpleNamespace(DIAGNOSTIC="diagnostic")),
            "homeassistant.helpers.update_coordinator": module("homeassistant.helpers.update_coordinator",
                CoordinatorEntity=CoordinatorEntity),
        }
        with patch.dict(sys.modules, modules):
            sensor = importlib.import_module("vdsina_unit_test.sensor")
            coordinator = types.SimpleNamespace(
                entry=types.SimpleNamespace(unique_id="userapi.vdsina.com:7"),
                data={"currency": "USD", "forecast": "2026-10-28", "servers": {},
                      "last_update": datetime.now(timezone.utc)},
            )
            for key in sensor.ACCOUNT_SENSORS:
                with self.subTest(key=key):
                    entity = sensor.VDSinaAccountSensor(coordinator, key)
                    expected = "USD" if key in ("balance", "bonus", "partner") else "d" if key == "days_left" else None
                    self.assertEqual(entity.native_unit_of_measurement, expected)
            forecast = sensor.VDSinaAccountSensor(coordinator, "forecast")
            self.assertEqual(forecast._attr_device_class, "date")
            self.assertEqual(forecast.native_value, date(2026, 10, 28))
            coordinator.data["forecast"] = None
            self.assertIsNone(forecast.native_value)
            api = importlib.import_module("vdsina_unit_test.api")
            coordinator.data["servers"]["10"] = api.normalize_server({
                "id": 10, "name": "Test VPS", "server-plan": {"name": "2 RAM / 1 CPU / 40 NVMe"},
                "bandwidth": {"current_month": 12_500_000_000, "last_month": 2_000_000_000},
            }, None)
            tariff = sensor.VDSinaServerSensor(coordinator, "10", "tariff")
            traffic = sensor.VDSinaServerSensor(coordinator, "10", "traffic_usage")
            self.assertEqual(tariff.native_value, "2 RAM / 1 CPU / 40 NVMe")
            self.assertIsNone(tariff.native_unit_of_measurement)
            self.assertAlmostEqual(traffic.native_value, 12_500_000_000 / (1024 ** 3))
            self.assertEqual(traffic.native_unit_of_measurement, "GiB")
            self.assertEqual(traffic._attr_device_class, "data_size")
            self.assertIn("использование трафика", traffic.name)
            coordinator.data["servers"]["10"]["bandwidth"]["current_month"] = 0
            self.assertEqual(traffic.native_value, 0)
            coordinator.data["servers"]["10"]["bandwidth"] = None
            self.assertIsNone(traffic.native_value)
            server = coordinator.data["servers"]["10"]
            server["traffic_limit_bytes"] = 1024 ** 4
            server["bandwidth"] = {"current_month": 130.7 * 1024 ** 3}
            server["traffic_statistics"] = {"incoming_bytes": 65.62 * 1024 ** 3, "outgoing_bytes": 65.08 * 1024 ** 3}
            incoming = sensor.VDSinaServerSensor(coordinator, "10", "traffic_incoming")
            outgoing = sensor.VDSinaServerSensor(coordinator, "10", "traffic_outgoing")
            limit = sensor.VDSinaServerSensor(coordinator, "10", "traffic_limit")
            percent = sensor.VDSinaServerSensor(coordinator, "10", "traffic_percent")
            self.assertAlmostEqual(incoming.native_value, 65.62)
            self.assertAlmostEqual(outgoing.native_value, 65.08)
            self.assertEqual(limit.native_value, 1)
            self.assertEqual(limit.native_unit_of_measurement, "TiB")
            self.assertEqual(round(percent.native_value, 2), 12.76)
            self.assertEqual(percent.native_unit_of_measurement, "%")
            server["traffic_limit_bytes"] = None
            self.assertIsNone(percent.native_value)


if __name__ == "__main__":
    unittest.main()

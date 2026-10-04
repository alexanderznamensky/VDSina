"""Account sensors and stable, dynamically discovered VPS entities."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory

from .api import days_left, parse_date, payment_message, traffic_gb, traffic_gib
from .const import DOMAIN
from .entity import VDSinaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VDSinaAccountSensor(coordinator, key) for key in ACCOUNT_SENSORS])
    known = set()

    @callback
    def discover():
        entities = []
        for server_id in coordinator.data["servers"]:
            if server_id not in known:
                known.add(server_id)
                entities.extend(VDSinaServerSensor(coordinator, server_id, key) for key in (
                    "balance", "status", "ip", "tariff", "traffic_usage", "traffic_incoming",
                    "traffic_outgoing", "traffic_limit", "traffic_percent"))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


ACCOUNT_SENSORS = {
    "balance": ("Баланс", "mdi:wallet"),
    "bonus": ("Бонусный баланс", "mdi:wallet-giftcard"),
    "partner": ("Партнёрский баланс", "mdi:handshake"),
    "forecast": ("Дата прогноза отключения", "mdi:calendar-alert"),
    "days_left": ("Осталось дней", "mdi:calendar-clock"),
    "server_count": ("Количество серверов", "mdi:server-network"),
    "last_update": ("Последнее обновление", "mdi:update"),
}


class VDSinaAccountSensor(VDSinaEntity, SensorEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{self.entry.unique_id}_{key}"
        self._attr_name, self._attr_icon = ACCOUNT_SENSORS[key]
        if key in ("balance", "bonus", "partner"):
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 2
        elif key == "forecast":
            self._attr_device_class = SensorDeviceClass.DATE
        elif key == "last_update":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_unit_of_measurement(self):
        if self.key in ("balance", "bonus", "partner"):
            return self.coordinator.data.get("currency")
        return "d" if self.key == "days_left" else None

    @property
    def native_value(self):
        data = self.coordinator.data
        if self.key == "forecast":
            return parse_date(data.get("forecast"))
        if self.key == "days_left":
            return days_left(data.get("forecast"))
        if self.key == "server_count":
            return len(data["servers"])
        return data.get(self.key)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {"provider": "VDSina", "account_id": data["account_id"],
                "last_update": data["last_update"].isoformat(),
                "execution_seconds": data["execution_seconds"]}


class VDSinaServerSensor(VDSinaEntity, SensorEntity):
    def __init__(self, coordinator, server_id, key):
        super().__init__(coordinator)
        self.server_id = server_id
        self.key = key
        self._initial_name = coordinator.data["servers"][server_id]["name"]
        self._attr_unique_id = f"{self.entry.unique_id}_server_{server_id}_{key}"
        self._attr_icon = {"balance": "mdi:server-network", "status": "mdi:server", "ip": "mdi:ip-network",
                           "tariff": "mdi:tag-outline", "traffic_usage": "mdi:swap-vertical",
                           "traffic_incoming": "mdi:download-network", "traffic_outgoing": "mdi:upload-network",
                           "traffic_limit": "mdi:database", "traffic_percent": "mdi:percent"}[key]
        if key == "balance":
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 2
        elif key in ("traffic_usage", "traffic_incoming", "traffic_outgoing", "traffic_limit"):
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 1 if key == "traffic_usage" else 2
            self._attr_suggested_unit_of_measurement = "TiB" if key == "traffic_limit" else "GiB"
        elif key == "traffic_percent":
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 2

    @property
    def _server(self):
        return self.coordinator.data["servers"].get(self.server_id)

    @property
    def name(self):
        server = self._server
        name = server["name"] if server else self._initial_name
        names = {"balance": "баланс", "status": "статус", "ip": "IP", "tariff": "тариф",
                 "traffic_usage": "использование трафика", "traffic_incoming": "входящий трафик",
                 "traffic_outgoing": "исходящий трафик", "traffic_limit": "лимит трафика",
                 "traffic_percent": "использовано трафика в процентах"}
        return f"{name} {names[self.key]}"

    @property
    def available(self):
        return super().available and self._server is not None

    @property
    def native_unit_of_measurement(self):
        if self.key == "balance":
            return self.coordinator.data.get("currency")
        if self.key == "traffic_limit":
            return "TiB"
        if self.key == "traffic_percent":
            return "%"
        return "GiB" if self.key in ("traffic_usage", "traffic_incoming", "traffic_outgoing") else None

    @property
    def native_value(self):
        if self._server is None:
            return None
        if self.key == "traffic_usage":
            return traffic_gib(self._server.get("bandwidth"))
        if self.key in ("traffic_incoming", "traffic_outgoing"):
            direction = "incoming_bytes" if self.key == "traffic_incoming" else "outgoing_bytes"
            value = (self._server.get("traffic_statistics") or {}).get(direction)
            return value / (1024 ** 3) if value is not None else None
        if self.key == "traffic_limit":
            value = self._server.get("traffic_limit_bytes")
            return value / (1024 ** 4) if value is not None else None
        if self.key == "traffic_percent":
            used = traffic_gb(self._server.get("bandwidth"))
            limit = self._server.get("traffic_limit_bytes")
            return used * 1_000_000_000 / limit * 100 if used is not None and limit else None
        return self.coordinator.data.get("balance") if self.key == "balance" else self._server.get(self.key)

    @property
    def extra_state_attributes(self):
        server = self._server
        if not server:
            return {}
        data = self.coordinator.data
        forecast = parse_date(data.get("forecast"))
        return {
            "provider": "VDSina", "IP": server["ip"], "Order": server["id"],
            "server_id": server["id"], "ips": server["ips"],
            "status": server["status"], "status_text": server["status_text"],
            "due_date": forecast.strftime("%d.%m.%Y") if forecast else None,
            "forecast": data.get("forecast"), "days_left": days_left(data.get("forecast")),
            "message": payment_message(server["name"], data.get("forecast")),
            "balance": data.get("balance"), "currency": data.get("currency"),
            "tariff": server["tariff"], "datacenter": server["datacenter"],
            "country": server["country"], "hostname": server["hostname"], "os": server["os"],
            "cpu": server["cpu"], "ram": server["ram"], "disk": server["disk"],
            "traffic_limit": server["traffic_limit"], "bandwidth": server["bandwidth"],
            "traffic_period": "current_month",
            "traffic_current_month_gb": traffic_gb(server["bandwidth"]),
            "traffic_last_month_gb": traffic_gb(server["bandwidth"], "last_month"),
            "traffic_current_month_gib": traffic_gib(server["bandwidth"]),
            "traffic_last_month_gib": traffic_gib(server["bandwidth"], "last_month"),
            "traffic_limit_bytes": server.get("traffic_limit_bytes"),
            "traffic_statistics": server.get("traffic_statistics"),
            "traffic_statistics_period": server.get("traffic_statistics_period"),
            "traffic_statistics_error": server.get("traffic_statistics_error"),
            "service_end": server["service_end"], "autopay": server["autoprolong"],
            "autoprolong": server["autoprolong"], "autorun": server["autorun"],
            "last_update": data["last_update"].isoformat(),
            "execution_seconds": data["execution_seconds"],
        }

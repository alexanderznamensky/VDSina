"""Shared account-device metadata."""
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_API_HOST, DOMAIN


class VDSinaEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.entry = coordinator.entry

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.unique_id)},
            name=self.entry.title, manufacturer="VDSina", model="Hosting Account",
            configuration_url=f"https://cp.{self.entry.data[CONF_API_HOST].removeprefix('userapi.')}/vds/list",
        )

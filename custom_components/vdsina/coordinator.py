"""One scheduled refresh for all VDSina entities."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import InvalidAuth, VDSinaClient, VDSinaError
from .const import CONF_API_HOST, CONF_API_TOKEN, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class VDSinaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(minutes=interval))
        self.entry = entry
        self.client = VDSinaClient(async_get_clientsession(hass), entry.data[CONF_API_TOKEN], entry.data[CONF_API_HOST])

    async def _async_update_data(self) -> dict[str, Any]:
        started = time.monotonic()
        try:
            data = await self.client.snapshot()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed("VDSina API token rejected") from err
        except (VDSinaError, TimeoutError) as err:
            raise UpdateFailed(str(err) or "VDSina API timeout") from err
        # Guard against replacing the token with one belonging to another account.
        expected = f"{self.client.host}:{data['account_id']}"
        if self.entry.unique_id != expected:
            raise ConfigEntryAuthFailed("VDSina token belongs to another account")
        data["last_update"] = dt_util.utcnow()
        data["execution_seconds"] = round(time.monotonic() - started, 2)
        return data

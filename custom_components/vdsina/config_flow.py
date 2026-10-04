"""UI setup, token reauthentication and polling options."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector, SelectSelectorConfig, SelectSelectorMode,
    TextSelector, TextSelectorConfig, TextSelectorType,
)

from .api import InvalidAuth, VDSinaClient, VDSinaError
from .const import (API_HOSTS, CONF_API_HOST, CONF_API_TOKEN, CONF_SCAN_INTERVAL,
                    DEFAULT_API_HOST, DEFAULT_SCAN_INTERVAL, DOMAIN)

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
INTERVAL = vol.All(vol.Coerce(int), vol.Range(min=5, max=1440))


class VDSinaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                client = VDSinaClient(async_get_clientsession(self.hass), user_input[CONF_API_TOKEN], user_input[CONF_API_HOST])
                data = await client.snapshot()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except (VDSinaError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{client.host}:{data['account_id']}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"VDSina {data['account_name'] or data['account_id']}",
                    data={**user_input, CONF_API_TOKEN: client._token},
                )
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_API_HOST, default=(user_input or {}).get(CONF_API_HOST, DEFAULT_API_HOST)):
                SelectSelector(SelectSelectorConfig(options=list(API_HOSTS), mode=SelectSelectorMode.DROPDOWN)),
            vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR,
            vol.Optional(CONF_SCAN_INTERVAL, default=(user_input or {}).get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): INTERVAL,
        }))

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                client = VDSinaClient(async_get_clientsession(self.hass), user_input[CONF_API_TOKEN], entry.data[CONF_API_HOST])
                account = await client.account()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except (VDSinaError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if f"{client.host}:{account['account']['id']}" != entry.unique_id:
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_API_TOKEN: client._token})
        return self.async_show_form(step_id="reauth_confirm", errors=errors,
                                   data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return VDSinaOptionsFlow()


class VDSinaOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            token = user_input.get(CONF_API_TOKEN, "").strip()
            if token:
                try:
                    client = VDSinaClient(async_get_clientsession(self.hass), token, self.config_entry.data[CONF_API_HOST])
                    account = await client.account()
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except (VDSinaError, TimeoutError):
                    errors["base"] = "cannot_connect"
                else:
                    if f"{client.host}:{account['account']['id']}" != self.config_entry.unique_id:
                        errors["base"] = "wrong_account"
                    else:
                        self.hass.config_entries.async_update_entry(
                            self.config_entry,
                            data={**self.config_entry.data, CONF_API_TOKEN: client._token},
                            options={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]},
                        )
            if not errors:
                return self.async_create_entry(title="", data={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]})
        interval = self.config_entry.options.get(CONF_SCAN_INTERVAL, self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        return self.async_show_form(step_id="init", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_SCAN_INTERVAL, default=interval): INTERVAL,
            vol.Optional(CONF_API_TOKEN): TOKEN_SELECTOR,
        }))

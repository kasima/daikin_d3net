"""Config flow for Daikin DIII-NET Modbus integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    ADAPTER_DCPA01,
    ADAPTER_DTA116A51,
    CONF_ADAPTER,
    CONF_PROTOCOL,
    CONF_SLAVE,
    DEFAULT_ADAPTER,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SLAVE,
    DOMAIN,
    PROTOCOL_RTU_OVER_TCP,
    PROTOCOL_TCP,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_SLAVE, default=DEFAULT_SLAVE): int,
        vol.Optional(CONF_PROTOCOL, default=PROTOCOL_TCP): SelectSelector(
            SelectSelectorConfig(
                options=[
                    {"value": PROTOCOL_TCP, "label": "Modbus TCP"},
                    {"value": PROTOCOL_RTU_OVER_TCP, "label": "Modbus RTU over TCP"},
                ],
                mode=SelectSelectorMode.LIST,
            )
        ),
        vol.Optional(CONF_ADAPTER, default=DEFAULT_ADAPTER): SelectSelector(
            SelectSelectorConfig(
                options=[
                    {"value": ADAPTER_DTA116A51, "label": "DTA116A51 / EKMBDXB7V1"},
                    {"value": ADAPTER_DCPA01, "label": "DCPA01 (empirical, see docs)"},
                ],
                mode=SelectSelectorMode.LIST,
            )
        ),
    }
)


@callback
def daikin_d3net_hosts(hass: HomeAssistant):
    """Return the hosts already configured."""
    return set(
        entry.data[CONF_HOST] for entry in hass.config_entries.async_entries(DOMAIN)
    )


class D3netConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Daikin DIII-NET Modbus."""

    VERSION = 1

    def _host_in_configuration_exists(self, host) -> bool:
        """Return True if host exists in configuration."""
        if host in daikin_d3net_hosts(self.hass):
            return True
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            if self._host_in_configuration_exists(host):
                errors[CONF_HOST] = "already_configured"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

"""Config flow for the ProfiCare PC-PW 3008 BT scale.

The scale is powered down and radio-silent almost all the time, so a flow that
checks Home Assistant's discovery cache once and aborts is useless in practice:
the user has to be *told* to wake the scale, and then given time to do it.

So the flow is: instructions -> a timed scan with a spinner -> retry on
timeout, never a dead end.
"""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_process_advertisements,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AGE,
    CONF_HEIGHT,
    CONF_MALE,
    CONF_SLOT,
    DEFAULT_AGE,
    DEFAULT_HEIGHT,
    DEFAULT_SLOT,
    DISCOVERY_TIMEOUT,
    DOMAIN,
    LOCAL_NAME,
    MAX_SLOT,
)

PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MALE, default=True): selector.BooleanSelector(),
        vol.Required(CONF_AGE, default=DEFAULT_AGE): selector.NumberSelector(
            selector.NumberSelectorConfig(min=10, max=120, step=1, mode="box")
        ),
        vol.Required(CONF_HEIGHT, default=DEFAULT_HEIGHT): selector.NumberSelector(
            selector.NumberSelectorConfig(min=100, max=230, step=1, mode="box")
        ),
        vol.Required(CONF_SLOT, default=DEFAULT_SLOT): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=MAX_SLOT, step=1, mode="box")
        ),
    }
)


def _coerce_profile(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_MALE: bool(user_input[CONF_MALE]),
        CONF_AGE: int(user_input[CONF_AGE]),
        CONF_HEIGHT: int(user_input[CONF_HEIGHT]),
        CONF_SLOT: int(user_input[CONF_SLOT]),
    }


class PcPw3008ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Wake the scale, find it, then collect the profile it needs."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._scan_task: asyncio.Task[BluetoothServiceInfoBleak | None] | None = None
        self._scan_failed = False

    # --- automatic discovery --------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Home Assistant spotted the scale advertising on its own."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self.context["title_placeholders"] = {"name": LOCAL_NAME}
        return await self.async_step_profile()

    # --- manual add -----------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain how to wake the scale, then scan for it."""
        if user_input is not None:
            self._scan_failed = False
            return await self.async_step_scan()

        # If it happens to be awake already, offer it straight away.
        already_seen = self._known_candidates()
        if already_seen and not self._scan_failed:
            return self.async_show_form(
                step_id="pick",
                data_schema=vol.Schema(
                    {vol.Required(CONF_ADDRESS): vol.In(already_seen)}
                ),
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            errors={"base": "not_found"} if self._scan_failed else None,
            description_placeholders={"seconds": str(int(DISCOVERY_TIMEOUT))},
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose among scales that are already advertising."""
        if user_input is None:
            return await self.async_step_user()
        self._address = user_input[CONF_ADDRESS]
        await self.async_set_unique_id(self._address, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return await self.async_step_profile()

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Watch for the scale for a fixed window, showing a spinner."""
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(self._async_wait_for_scale())

        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan",
                progress_action="scanning",
                progress_task=self._scan_task,
            )

        try:
            info = self._scan_task.result()
        except Exception:  # noqa: BLE001 - never strand the user on an exception
            info = None
        finally:
            self._scan_task = None

        if info is None:
            # Back to the instructions, with an error and a Submit to retry.
            self._scan_failed = True
            return self.async_show_progress_done(next_step_id="user")

        self._address = info.address
        return self.async_show_progress_done(next_step_id="found")

    async def async_step_found(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Landing step after a successful scan."""
        assert self._address is not None
        await self.async_set_unique_id(self._address, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return await self.async_step_profile()

    async def _async_wait_for_scale(self) -> BluetoothServiceInfoBleak | None:
        """Block until the scale advertises, or the window closes."""

        def _matcher(info: BluetoothServiceInfoBleak) -> bool:
            return info.name == LOCAL_NAME and info.address not in self._async_current_ids()

        try:
            return await async_process_advertisements(
                self.hass,
                _matcher,
                {"local_name": LOCAL_NAME, "connectable": True},
                BluetoothScanningMode.ACTIVE,
                DISCOVERY_TIMEOUT,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return None

    def _known_candidates(self) -> dict[str, str]:
        configured = self._async_current_ids()
        return {
            info.address: f"{info.name or LOCAL_NAME} ({info.address})"
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.name == LOCAL_NAME and info.address not in configured
        }

    # --- profile --------------------------------------------------------------

    async def async_step_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The scale computes body composition itself from this profile."""
        if user_input is None:
            return self.async_show_form(step_id="profile", data_schema=PROFILE_SCHEMA)

        assert self._address is not None
        return self.async_create_entry(
            title=LOCAL_NAME,
            data={CONF_ADDRESS: self._address, **_coerce_profile(user_input)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PcPw3008OptionsFlow()


class PcPw3008OptionsFlow(OptionsFlow):
    """Let the profile be corrected without re-pairing."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_coerce_profile(user_input))

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                PROFILE_SCHEMA,
                {
                    CONF_MALE: current.get(CONF_MALE, True),
                    CONF_AGE: current.get(CONF_AGE, DEFAULT_AGE),
                    CONF_HEIGHT: current.get(CONF_HEIGHT, DEFAULT_HEIGHT),
                    CONF_SLOT: current.get(CONF_SLOT, DEFAULT_SLOT),
                },
            ),
        )

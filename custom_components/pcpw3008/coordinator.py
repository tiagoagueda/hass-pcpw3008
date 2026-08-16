"""Connection handling for the ProfiCare PC-PW 3008 BT scale.

The scale sleeps most of the time and is unreachable while asleep, so there is
nothing to poll. Instead we let Home Assistant's Bluetooth stack tell us the
moment it starts advertising (i.e. someone stepped on it), then connect and run
a single short session.

Session shape, taken from a captured exchange with real hardware:

    connect                              (~2.7s once the scale is awake)
    subscribe 0xFFB2
    write user profile  -> ACK
    write unit          -> ACK *or nothing at all*
    ... live frames while the user settles ...
    final frame                          (~15s after connect)
    write measurement-done               -> scale powers off

The unit ACK is genuinely optional: on one capture the scale answered it, on
another it skipped straight to streaming. So the handshake is fire-and-forget
and the live/final frames drive everything.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import protocol as p
from .const import (
    CHAR_UUID,
    DEDUPE_WINDOW,
    DOMAIN,
    SESSION_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Profile:
    """Body profile the scale needs before it can compute composition."""

    male: bool
    age: int
    height_cm: int
    slot: int = 0


class ScaleCoordinator(DataUpdateCoordinator[p.FinalFrame]):
    """Owns the BLE session and publishes the last settled measurement."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        profile: Profile,
    ) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {address}")
        self.address = address
        self.profile = profile
        self.live_weight: float | None = None

        self._session_lock = asyncio.Lock()
        self._divisor = 10
        self._final: p.FinalFrame | None = None
        self._final_event = asyncio.Event()
        self._last_published: tuple[float, float] | None = None  # (weight, ts)
        self._unsubscribe: callback | None = None

    # --- lifecycle ------------------------------------------------------------

    async def async_start(self) -> None:
        """Watch for the scale waking up."""
        self._unsubscribe = bluetooth.async_register_callback(
            self.hass,
            self._on_advertisement,
            {"address": self.address, "connectable": True},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )

    async def async_stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @callback
    def _on_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """The scale is awake. Start a session unless one is already running."""
        if self._session_lock.locked():
            return
        self.hass.async_create_task(self._run_session(service_info.device))

    # --- one weigh-in ---------------------------------------------------------

    async def _run_session(self, device: BLEDevice) -> None:
        if self._session_lock.locked():
            return
        async with self._session_lock:
            self._final = None
            self._final_event.clear()
            client = None
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self.address,
                    max_attempts=4,
                )
                _LOGGER.debug("Connected to %s", self.address)

                await client.start_notify(CHAR_UUID, self._on_notify)

                # Fire-and-forget: the scale may or may not ACK these.
                await client.write_gatt_char(
                    CHAR_UUID,
                    p.build_user(
                        male=self.profile.male,
                        age=self.profile.age,
                        height_cm=self.profile.height_cm,
                        slot=self.profile.slot,
                    ),
                    response=True,
                )
                await client.write_gatt_char(CHAR_UUID, p.build_unit(), response=True)

                try:
                    await asyncio.wait_for(
                        self._final_event.wait(), timeout=SESSION_TIMEOUT
                    )
                except TimeoutError:
                    _LOGGER.debug("No settled measurement before timeout")
                    return

                # Tell the scale we're done so it powers off promptly.
                try:
                    await client.write_gatt_char(
                        CHAR_UUID, p.build_done(), response=True
                    )
                except Exception:  # noqa: BLE001 - best effort, scale may be gone
                    pass

            except Exception as err:  # noqa: BLE001 - never kill the callback
                _LOGGER.debug("Session with %s failed: %s", self.address, err)
            finally:
                self.live_weight = None
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass

    @callback
    def _on_notify(self, _characteristic, data: bytearray) -> None:
        frame = bytes(data)
        if not p.is_valid(frame):
            _LOGGER.debug("Dropping malformed frame: %s", frame.hex())
            return

        resp = frame[1]
        if resp == p.RESP_ACK:
            return

        if resp == p.RESP_LIVE:
            live = p.parse_live(frame)
            if live is not None:
                # The scale states its own resolution; trust it over a default.
                self._divisor = live.divisor
                self.live_weight = live.weight_kg
            return

        if resp == p.RESP_FINAL:
            final = p.parse_final(frame, divisor=self._divisor)
            if final is None or final.weight_kg <= 0:
                return
            if self._is_duplicate(final.weight_kg):
                return
            self._last_published = (final.weight_kg, time.monotonic())
            self._final = final
            self._final_event.set()
            self.async_set_updated_data(final)

    def _is_duplicate(self, weight: float) -> bool:
        """The scale repeats the final frame; publish a measurement only once."""
        if self._last_published is None:
            return False
        prev_weight, prev_ts = self._last_published
        return (
            abs(prev_weight - weight) < 0.05
            and (time.monotonic() - prev_ts) < DEDUPE_WINDOW
        )

    async def _async_update_data(self) -> p.FinalFrame:
        """Never polled — data arrives via async_set_updated_data."""
        if self._final is None:
            raise TimeoutError("No measurement yet")
        return self._final

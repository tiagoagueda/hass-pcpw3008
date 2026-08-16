"""Connection handling and per-person attribution for the PC-PW 3008 BT.

The scale sleeps and is unreachable most of the time, so there is nothing to
poll. Home Assistant's Bluetooth stack tells us the moment it starts
advertising — i.e. someone stepped on it — and we run one short session.

Session shape, from captures against real hardware::

    connect                              (~2.7s once the scale is awake)
    subscribe 0xFFB2
    write user profile  -> ACK
    write unit          -> ACK *or nothing at all*
    ... live frames while the user settles ...
    final frame                          (~15s after connect)
    write measurement-done               -> scale powers off

The unit ACK is genuinely optional — one capture answered it, another skipped
straight to streaming — so the handshake is fire-and-forget and the frames drive
everything.

## Who is on the scale

The scale computes body composition on-device from the profile pushed at the
*start* of a session, before anyone has stood on it. So the integration has to
guess. It pushes the profile of whoever weighed last, on the reasonable bet that
households repeat, then identifies the actual person from the settled weight.

When the guess was right the scale's own figures are correct and kept. When it
was wrong the composition was computed for someone else's body, so it is
discarded rather than shown under the wrong name; weight survives and BMI is
recomputed locally. See [person.attribute].
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import protocol as p
from .const import (
    CHAR_UUID,
    CONF_AGE,
    CONF_EXPECTED_WEIGHT,
    CONF_HEIGHT,
    CONF_MALE,
    CONF_NAME,
    CONF_SLOT,
    CONF_USER_ID,
    DEDUPE_WINDOW,
    DOMAIN,
    SESSION_TIMEOUT,
    SUBENTRY_PERSON,
)
from .person import Person, attribute, learn_weight, margin, match_by_weight

_LOGGER = logging.getLogger(__name__)


@dataclass
class Attribution:
    """Which person a measurement was assigned to, and how confidently."""

    person: Person
    profile_pushed_for: Person | None
    margin_kg: float | None
    exact: bool


class ScaleCoordinator(DataUpdateCoordinator[dict[str, p.FinalFrame]]):
    """Owns the BLE session and publishes per-person measurements."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {address}")
        self.entry = entry
        self.address = address
        self.live_weight: float | None = None
        self.last_attribution: Attribution | None = None

        self._session_lock = asyncio.Lock()
        self._divisor = 10
        self._final_event = asyncio.Event()
        self._last_published: tuple[float, float] | None = None
        self._unsubscribe = None
        self._last_person_id: str | None = None
        self.data = {}

    # --- people ---------------------------------------------------------------

    @property
    def people(self) -> list[Person]:
        """Household members, read fresh from the config subentries."""
        out: list[Person] = []
        for subentry in self.entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_PERSON:
                continue
            d = subentry.data
            out.append(
                Person(
                    subentry_id=subentry.subentry_id,
                    name=d.get(CONF_NAME) or subentry.title,
                    male=bool(d.get(CONF_MALE, True)),
                    age=int(d.get(CONF_AGE, 40)),
                    height_cm=int(d.get(CONF_HEIGHT, 175)),
                    slot=int(d.get(CONF_SLOT, 0)),
                    user_id=d.get(CONF_USER_ID),
                    expected_weight=d.get(CONF_EXPECTED_WEIGHT),
                )
            )
        return out

    def _person_to_push(self) -> Person | None:
        """Whoever weighed last — the best guess before anyone stands on it."""
        people = self.people
        if not people:
            return None
        for person in people:
            if person.subentry_id == self._last_person_id:
                return person
        return people[0]

    def _remember(self, person: Person, weight_kg: float) -> None:
        """Persist the learned weight so recognition improves over time."""
        subentry = self.entry.subentries.get(person.subentry_id)
        if subentry is None:
            return
        updated = learn_weight(person, weight_kg)
        if subentry.data.get(CONF_EXPECTED_WEIGHT) == updated.expected_weight:
            return
        self.hass.config_entries.async_update_subentry(
            self.entry,
            subentry,
            data={**subentry.data, CONF_EXPECTED_WEIGHT: updated.expected_weight},
        )

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
            pushed = self._person_to_push()
            if pushed is None:
                _LOGGER.debug("No people configured; nothing to attribute a weigh-in to")
                return

            self._pushed = pushed
            self._final_event.clear()
            self._disconnected = asyncio.Event()
            client = None
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self.address,
                    max_attempts=4,
                    disconnected_callback=self._on_disconnected,
                )
                _LOGGER.debug("Connected; pushing %s's profile (P%d)", pushed.name, pushed.slot)
                await client.start_notify(CHAR_UUID, self._on_notify)

                # Fire-and-forget: the scale may or may not ACK these.
                await client.write_gatt_char(
                    CHAR_UUID,
                    p.build_user(
                        male=pushed.male,
                        age=pushed.age,
                        height_cm=pushed.height_cm,
                        slot=pushed.slot,
                    ),
                    response=True,
                )
                await client.write_gatt_char(CHAR_UUID, p.build_unit(), response=True)

                # Whichever comes first: a measurement, or the scale hanging up.
                waiters = [
                    asyncio.create_task(self._final_event.wait()),
                    asyncio.create_task(self._disconnected.wait()),
                ]
                done, pending = await asyncio.wait(
                    waiters, timeout=SESSION_TIMEOUT,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if not self._final_event.is_set():
                    _LOGGER.debug(
                        "No settled measurement (%s). Stay on the scale until it "
                        "settles — the link needs a few seconds to come up first.",
                        "scale disconnected" if self._disconnected.is_set() else "timed out",
                    )
                    return

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

    def _on_disconnected(self, _client) -> None:
        """The scale hung up — usually because the user stepped off."""
        event = getattr(self, "_disconnected", None)
        if event is not None:
            self.hass.loop.call_soon_threadsafe(event.set)

    @callback
    def _on_notify(self, _characteristic, data: bytearray) -> None:
        # One notification can carry several frames back to back.
        payload = bytes(data)
        handled = False
        for frame in p.iter_frames(payload):
            handled = True
            self._handle_frame(frame)
        if not handled:
            _LOGGER.debug("Dropping unparseable notification: %s", payload.hex())

    @callback
    def _handle_frame(self, frame: bytes) -> None:
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
            _LOGGER.debug("Final frame: %.2f kg, contact=%s", final.weight_kg, final.has_contact)
            self._last_published = (final.weight_kg, time.monotonic())
            self._publish(final)
            self._final_event.set()

    @callback
    def _publish(self, final: p.FinalFrame) -> None:
        """Attribute the measurement to a person and push it to their sensors."""
        people = self.people
        pushed = getattr(self, "_pushed", None)
        person = match_by_weight(people, final.weight_kg) or pushed
        if person is None:
            return

        shaped = attribute(final, person, pushed)
        exact = pushed is not None and pushed.subentry_id == person.subentry_id
        if not exact:
            _LOGGER.info(
                "Weigh-in matched %s but %s's profile was pushed; keeping weight "
                "and recomputed BMI only",
                person.name,
                pushed.name if pushed else "nobody",
            )

        self.last_attribution = Attribution(
            person=person,
            profile_pushed_for=pushed,
            margin_kg=margin(people, final.weight_kg, person),
            exact=exact,
        )
        _LOGGER.debug(
            "Attributed %.2f kg to %s (composition valid: %s)",
            final.weight_kg, person.name, exact,
        )
        self._last_person_id = person.subentry_id
        self._remember(person, final.weight_kg)

        self.async_set_updated_data({**(self.data or {}), person.subentry_id: shaped})

    def reassign_last(self, subentry_id: str) -> bool:
        """Move the most recent measurement to a different person.

        Only weight and a recomputed BMI move across: the scale's composition
        figures were calculated for the original profile and cannot be redone
        for another body, so carrying them over would be a fabrication.
        """
        previous = self.last_attribution
        if previous is None:
            return False
        target = next(
            (p_ for p_ in self.people if p_.subentry_id == subentry_id), None
        )
        if target is None or target.subentry_id == previous.person.subentry_id:
            return False

        current = (self.data or {}).get(previous.person.subentry_id)
        if current is None:
            return False

        moved = attribute(current, target, previous.profile_pushed_for)
        data = {**(self.data or {})}
        data.pop(previous.person.subentry_id, None)
        data[target.subentry_id] = moved

        self.last_attribution = Attribution(
            person=target,
            profile_pushed_for=previous.profile_pushed_for,
            margin_kg=None,
            exact=(
                previous.profile_pushed_for is not None
                and previous.profile_pushed_for.subentry_id == target.subentry_id
            ),
        )
        self._last_person_id = target.subentry_id
        self._remember(target, current.weight_kg)
        self.async_set_updated_data(data)
        return True

    def _is_duplicate(self, weight: float) -> bool:
        """The scale repeats the final frame; publish a measurement only once."""
        if self._last_published is None:
            return False
        prev_weight, prev_ts = self._last_published
        return (
            abs(prev_weight - weight) < 0.05
            and (time.monotonic() - prev_ts) < DEDUPE_WINDOW
        )

    async def _async_update_data(self) -> dict[str, p.FinalFrame]:
        """Never polled — data arrives via async_set_updated_data."""
        return self.data or {}

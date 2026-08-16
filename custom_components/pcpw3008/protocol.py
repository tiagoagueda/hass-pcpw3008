"""
Wire protocol for the ProfiCare PC-PW 3008 BT (Chipsea "WeChat scale" firmware).

Pure functions only — no Bluetooth, no Home Assistant — so the framing and the
field offsets can be unit-tested without hardware.

Frame layout::

    FA <cmd/resp> <len> <payload...> <checksum>

``checksum`` is the XOR of every byte from index 1 up to but excluding itself,
so XOR-ing the whole frame from index 1 (checksum included) yields 0.

Offsets below were read off a live capture of the scale and cross-checked
against the vendor's "Dr.Curve+" app and openScale's HoffenBbs8107Handler::

    FA 02 11 3B 05 00 45 02 1C 01 8F 00 AA 0B 45 B5 01 05 01 37 9B
             └w──┘ mk └fat┘ └wat┘ └mus┘ └bmr┘ bn └bmi┘ └vis┘ ag ck
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

MAGIC = 0xFA

# App → scale
CMD_MEASUREMENT_DONE = 0x82
CMD_CHANGE_UNIT = 0x83
CMD_SEND_USER = 0x85

# Scale → app
RESP_LIVE = 0x01
RESP_FINAL = 0x02
RESP_ACK = 0x03

# Byte 5 of a final frame: whether the electrodes had skin contact.
CONTACT_OK = 0x00
CONTACT_NONE = 0x04

UNIT_KG = 0


@dataclass(frozen=True)
class LiveFrame:
    """An intermediate reading while the scale is still settling."""

    weight_kg: float
    divisor: int
    unit: str


@dataclass(frozen=True)
class FinalFrame:
    """A settled measurement.

    Body composition is computed *on the scale* from the profile pushed during
    the handshake, so every field except ``weight_kg`` is only as correct as
    that profile. When the electrodes see no skin contact the scale reports
    weight alone and the rest stay ``None``.
    """

    weight_kg: float
    has_contact: bool
    fat_pct: float | None = None
    water_pct: float | None = None
    muscle_pct: float | None = None
    bmr_kcal: int | None = None
    bone_kg: float | None = None
    bmi: float | None = None
    visceral_fat: float | None = None
    body_age: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def checksum(frame: bytes) -> int:
    """XOR of bytes 1..n-2 — the value that belongs in the last byte."""
    x = 0
    for b in frame[1:-1]:
        x ^= b
    return x


def is_valid(frame: bytes) -> bool:
    if len(frame) < 4 or frame[0] != MAGIC:
        return False
    x = 0
    for b in frame[1:]:
        x ^= b
    return x == 0


def build(cmd: int, payload: bytes) -> bytes:
    """Frame a command for writing to characteristic 0xFFB2."""
    out = bytearray([MAGIC, cmd, len(payload)]) + payload + b"\x00"
    out[-1] = checksum(bytes(out))
    return bytes(out)


def build_user(
    *, male: bool, age: int, height_cm: int, slot: int = 0
) -> bytes:
    """Push the profile the scale uses to compute body composition.

    ``slot`` is the on-scale user profile (P0, P1, ...) — the same selector the
    scale's own display and the vendor app use, where the app sends the selected
    user's index here. openScale hard-codes 0 and labels it an unknown "plan id".
    Keep it matched to the profile you normally weigh under so readings line up
    with the scale's own history.
    """
    return build(
        CMD_SEND_USER,
        bytes(
            [slot & 0xFF, 0x01 if male else 0x00, age & 0xFF, height_cm & 0xFF]
        ),
    )


def build_unit(unit: int = UNIT_KG) -> bytes:
    return build(CMD_CHANGE_UNIT, bytes([(1 + unit) & 0xFF, 0x00]))


def build_done() -> bytes:
    """Acknowledge the measurement; the scale powers down shortly after."""
    return build(CMD_MEASUREMENT_DONE, bytes([0x00]))


def iter_frames(data: bytes):
    """Split one notification into the frames it contains.

    A single GATT notification can carry more than one frame back to back —
    observed on hardware as an ACK immediately followed by a live frame::

        FA 03 01 00 02  FA 01 03 01 3B 05 3D

    Treating the payload as a single frame silently drops real measurements, so
    always feed notifications through here. Frames are self-describing: byte 2
    is the payload length, giving a total size of ``len + 4``.

    Yields only structurally complete, checksum-valid frames; trailing garbage
    is ignored.
    """
    offset = 0
    end = len(data)
    while offset + 4 <= end:
        if data[offset] != MAGIC:
            offset += 1
            continue
        size = data[offset + 2] + 4
        if offset + size > end:
            break
        frame = data[offset : offset + size]
        if is_valid(frame):
            yield frame
            offset += size
        else:
            offset += 1


def _u16le(data: bytes, idx: int) -> int | None:
    if idx + 1 >= len(data):
        return None
    return data[idx] | (data[idx + 1] << 8)


def _scaled(data: bytes, idx: int, divisor: float) -> float | None:
    raw = _u16le(data, idx)
    return None if raw is None else raw / divisor


def decode_attribute(attr: int) -> tuple[int, str]:
    """Split the live frame's attribute byte into (weight divisor, unit).

    Bit 0x80 selects 0.01 kg resolution; the low bits select the display unit.
    Captured hardware reports 0x01 — 0.1 kg steps, kilograms.
    """
    divisor = 100 if attr & 0x80 else 10
    if attr & 0x01:
        unit = "kg"
    elif attr & 0x02:
        unit = "lb"
    elif attr & 0x04:
        unit = "st:lb"
    else:
        unit = "kg"
    return divisor, unit


def parse_live(data: bytes) -> LiveFrame | None:
    """Parse ``FA 01 <len> <attr> <weight lo> <weight hi> <ck>``."""
    if len(data) < 6:
        return None
    divisor, unit = decode_attribute(data[3])
    raw = _u16le(data, 4)
    if raw is None:
        return None
    return LiveFrame(weight_kg=raw / divisor, divisor=divisor, unit=unit)


def parse_final(data: bytes, divisor: int = 10) -> FinalFrame | None:
    """Parse a settled measurement frame."""
    if len(data) < 6:
        return None
    weight = _scaled(data, 3, divisor)
    if weight is None:
        return None

    if data[5] != CONTACT_OK:
        # Barefoot contact missing — the scale sends weight and nothing else.
        return FinalFrame(weight_kg=weight, has_contact=False)

    bone_raw = data[14] if len(data) > 14 else None
    return FinalFrame(
        weight_kg=weight,
        has_contact=True,
        fat_pct=_scaled(data, 6, 10.0),
        water_pct=_scaled(data, 8, 10.0),
        muscle_pct=_scaled(data, 10, 10.0),
        bmr_kcal=_u16le(data, 12),
        bone_kg=None if bone_raw is None else bone_raw / 10.0,
        bmi=_scaled(data, 15, 10.0),
        visceral_fat=_scaled(data, 17, 10.0),
        body_age=data[19] if len(data) > 19 else None,
    )

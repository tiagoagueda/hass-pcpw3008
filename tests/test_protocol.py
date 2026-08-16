"""Protocol tests against frames captured from a real PC-PW 3008 BT.

Run with:  python -m pytest tests/ -q
(or plain `python tests/test_protocol.py` for a dependency-free check)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "pcpw3008"))

import protocol as p  # noqa: E402

# Captured 2026-08-16 from BC:A6:00:00:2B:6A, profile male/40/175cm.
FINAL = bytes.fromhex("FA02113B0500450 21C018F00AA0B45B5010501379B".replace(" ", ""))
LIVE_ZERO = bytes.fromhex("FA0103010000 03".replace(" ", ""))
LIVE_SETTLED = bytes.fromhex("FA0103013B053D")
ACK = bytes.fromhex("FA03010002")


def test_captured_frames_validate():
    assert p.is_valid(FINAL)
    assert p.is_valid(LIVE_ZERO)
    assert p.is_valid(LIVE_SETTLED)
    assert p.is_valid(ACK)


def test_rejects_corrupted_checksum():
    bad = bytearray(FINAL)
    bad[3] ^= 0xFF          # change the weight, leave the checksum stale
    assert not p.is_valid(bytes(bad))


def test_rejects_foreign_frame():
    assert not p.is_valid(b"\x01\x02\x03\x04")
    assert not p.is_valid(b"")


def test_parses_final_measurement():
    m = p.parse_final(FINAL)
    assert m is not None
    assert m.has_contact is True
    assert m.weight_kg == 133.9
    assert m.fat_pct == 58.1
    assert m.water_pct == 28.4
    assert m.muscle_pct == 14.3
    assert m.bmr_kcal == 2986
    assert m.bone_kg == 6.9
    assert m.bmi == 43.7
    assert m.visceral_fat == 26.1
    assert m.body_age == 55


def test_no_electrode_contact_yields_weight_only():
    frame = bytearray(FINAL)
    frame[5] = p.CONTACT_NONE
    frame[-1] = p.checksum(bytes(frame))
    m = p.parse_final(bytes(frame))
    assert m.has_contact is False
    assert m.weight_kg == 133.9
    assert m.fat_pct is None
    assert m.body_age is None


def test_parses_live_frames():
    assert p.parse_live(LIVE_ZERO).weight_kg == 0.0
    settled = p.parse_live(LIVE_SETTLED)
    assert settled.weight_kg == 133.9
    assert settled.divisor == 10
    assert settled.unit == "kg"


def test_attribute_byte_selects_resolution_and_unit():
    # Real hardware reports 0x01. The high bit would mean 0.01 kg steps.
    assert p.decode_attribute(0x01) == (10, "kg")
    assert p.decode_attribute(0x81) == (100, "kg")
    assert p.decode_attribute(0x02) == (10, "lb")
    assert p.decode_attribute(0x04) == (10, "st:lb")


def test_commands_match_what_the_scale_accepted():
    # Byte-for-byte the frames the scale ACKed during the capture (slot P0).
    assert p.build_user(male=True, age=40, height_cm=175, slot=0).hex().upper() == "FA8504000128AF07"
    assert p.build_unit().hex().upper() == "FA8302010080"
    assert p.build_done().hex().upper() == "FA82010083"


def test_profile_slot_lands_in_the_first_payload_byte():
    # The vendor app puts the selected user index here; openScale hard-codes 0.
    for slot in range(8):
        frame = p.build_user(male=True, age=41, height_cm=174, slot=slot)
        assert frame[3] == slot
        assert p.is_valid(frame)


def test_built_commands_are_self_consistent():
    for frame in (
        p.build_user(male=False, age=31, height_cm=162),
        p.build_unit(),
        p.build_done(),
    ):
        assert p.is_valid(frame)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"PASS {name}")
    print(f"\n{passed} passed")

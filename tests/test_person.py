"""Tests for per-person matching and attribution.

Pure logic, no Home Assistant: these encode the rules that decide whose sensors
a weigh-in lands on, and what survives when the guess was wrong.
"""

import importlib
import sys
import types
from pathlib import Path

# person.py uses a package-relative import, as Home Assistant integrations must.
# Register a stand-in parent package pointing at the integration directory so the
# relative import resolves without executing __init__.py — which would drag in
# Home Assistant itself and defeat the point of testing pure logic.
_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "pcpw3008"
if "pcpw3008" not in sys.modules:
    _pkg = types.ModuleType("pcpw3008")
    _pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["pcpw3008"] = _pkg

pr = importlib.import_module("pcpw3008.person")
FinalFrame = importlib.import_module("pcpw3008.protocol").FinalFrame

TIAGO = pr.Person(
    subentry_id="a", name="Tiago", male=True, age=41, height_cm=174,
    slot=0, expected_weight=134.0,
)
MARCO = pr.Person(
    subentry_id="b", name="Marco", male=True, age=12, height_cm=150,
    slot=1, expected_weight=45.0,
)
NEWCOMER = pr.Person(
    subentry_id="c", name="Joana", male=False, age=38, height_cm=165, slot=2,
)

FULL = FinalFrame(
    weight_kg=133.9, has_contact=True, fat_pct=59.0, water_pct=27.8,
    muscle_pct=13.9, bmr_kcal=2986, bone_kg=6.9, bmi=44.2,
    visceral_fat=26.1, body_age=56,
)


def test_matches_nearest_expected_weight():
    assert pr.match_by_weight([TIAGO, MARCO], 133.9).name == "Tiago"
    assert pr.match_by_weight([TIAGO, MARCO], 46.2).name == "Marco"


def test_single_person_always_matches_even_without_a_known_weight():
    assert pr.match_by_weight([NEWCOMER], 70.0).name == "Joana"


def test_person_without_expected_weight_is_not_guessed_at():
    # Joana has no weight on file, so a reading goes to a known person instead.
    assert pr.match_by_weight([TIAGO, MARCO, NEWCOMER], 44.0).name == "Marco"


def test_no_people_means_no_match():
    assert pr.match_by_weight([], 80.0) is None


def test_composition_survives_when_the_right_profile_was_pushed():
    out = pr.attribute(FULL, TIAGO, TIAGO)
    assert out.fat_pct == 59.0
    assert out.body_age == 56
    assert out.bmi == 44.2


def test_composition_is_dropped_when_the_wrong_profile_was_pushed():
    # Marco stepped on, but the scale computed for Tiago's body.
    out = pr.attribute(FULL, MARCO, TIAGO)
    assert out.weight_kg == 133.9          # measured, profile-independent
    assert out.fat_pct is None             # would be a fabrication
    assert out.water_pct is None
    assert out.muscle_pct is None
    assert out.bmr_kcal is None
    assert out.body_age is None
    # BMI is arithmetic, so it can honestly be redone for the right height.
    assert out.bmi == round(133.9 / 1.50 ** 2, 1)


def test_bmi_matches_the_scales_own_arithmetic():
    # The scale reported 44.2 for 133.9 kg at 174 cm; we must agree.
    assert TIAGO.bmi(133.9) == 44.2


def test_margin_reports_how_close_the_runner_up_was():
    # 133.9 is 0.1 from Tiago and 88.9 from Marco -> a wide, confident margin.
    assert pr.margin([TIAGO, MARCO], 133.9, TIAGO) == 88.8
    # With nobody to compare against there is no margin to report.
    assert pr.margin([TIAGO], 133.9, TIAGO) is None


def test_learning_updates_the_expected_weight():
    learned = pr.learn_weight(TIAGO, 135.44)
    assert learned.expected_weight == 135.44
    assert learned.name == "Tiago"          # everything else untouched
    assert TIAGO.expected_weight == 134.0   # original is immutable


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"PASS {name}")
    print(f"\n{passed} passed")

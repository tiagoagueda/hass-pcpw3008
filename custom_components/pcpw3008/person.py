"""People bound to the scale's on-device profile slots.

The scale computes body composition itself, from a profile pushed at the start
of a session, and returns only finished numbers. That has two consequences the
whole multi-user design turns on:

1. Whose profile is pushed must be decided *before* anyone steps on, so
   identification after the fact can only ever recover weight — never fat,
   water, muscle, BMR or body age.
2. BMI is the exception: it is plain arithmetic on weight and height, so it can
   be recomputed locally for whoever actually stood on the scale.

Everything here is pure so it can be tested without Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .protocol import FinalFrame


@dataclass(frozen=True)
class Person:
    """One household member, bound to an on-scale profile slot."""

    subentry_id: str
    name: str
    male: bool
    age: int
    height_cm: int
    slot: int
    #: Home Assistant user this person is associated with. Admin-only to set;
    #: everyone else sees the association read-only, because config flows are
    #: already admin-gated by Home Assistant itself.
    user_id: str | None = None
    #: Last confirmed weight, used to recognise this person next time. Learned
    #: from measurements, so it tracks a changing body over time.
    expected_weight: float | None = None

    def bmi(self, weight_kg: float) -> float | None:
        """BMI for this person's height — recomputable, unlike the rest."""
        if self.height_cm <= 0:
            return None
        metres = self.height_cm / 100
        return round(weight_kg / (metres * metres), 1)


def match_by_weight(people: list[Person], weight_kg: float) -> Person | None:
    """Pick whoever's expected weight is nearest.

    Deliberately has no "too far away" cutoff: a reading is always attributed to
    somebody, and a wrong guess is corrected afterwards by reassigning it. A
    silently dropped weigh-in is worse than a reassignable one.

    Someone with no expected weight yet cannot be matched by weight, so they are
    only chosen when they are the only candidate.
    """
    if not people:
        return None
    if len(people) == 1:
        return people[0]

    known = [p for p in people if p.expected_weight is not None]
    if not known:
        return None
    return min(known, key=lambda p: abs(p.expected_weight - weight_kg))


def margin(people: list[Person], weight_kg: float, chosen: Person) -> float | None:
    """How much closer the chosen person is than the runner-up, in kg.

    Surfaced as an attribute so an ambiguous attribution is visible rather than
    presented with false confidence. ``None`` when there is nobody to compare to.
    """
    others = [
        p for p in people
        if p.subentry_id != chosen.subentry_id and p.expected_weight is not None
    ]
    if not others or chosen.expected_weight is None:
        return None
    best_other = min(abs(p.expected_weight - weight_kg) for p in others)
    return round(abs(best_other - abs(chosen.expected_weight - weight_kg)), 2)


def attribute(
    frame: FinalFrame, person: Person, profile_pushed_for: Person | None
) -> FinalFrame:
    """Shape a measurement for the person actually identified.

    When the pushed profile was theirs, the scale's own figures are correct and
    pass through untouched. Otherwise the composition was computed for somebody
    else's body, so it is dropped rather than shown under the wrong name —
    weight survives, and BMI is recomputed for the right height.
    """
    same = (
        profile_pushed_for is not None
        and profile_pushed_for.subentry_id == person.subentry_id
    )
    if same:
        return frame

    return FinalFrame(
        weight_kg=frame.weight_kg,
        has_contact=frame.has_contact,
        bmi=person.bmi(frame.weight_kg),
    )


def learn_weight(person: Person, weight_kg: float) -> Person:
    """Remember this weight so the person is recognised next time."""
    return replace(person, expected_weight=round(weight_kg, 2))

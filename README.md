# ProfiCare PC-PW 3008 BT — Home Assistant integration

Reads weight and body composition from a **ProfiCare PC-PW 3008 BT** bathroom
scale over Bluetooth LE, with no vendor cloud and no phone app involved.

The same firmware ships under other brands (it is the Chipsea "WeChat scale"
platform, also sold as the Hoffen BBS-8107), so other rebrands may work by
changing `LOCAL_NAME` in `const.py`.

## Sensors

| Sensor | Notes |
|---|---|
| Weight | kg, the only value measured directly |
| Body fat, Water, Muscle | % — computed on the scale, see below |
| Bone mass | kg |
| Basal metabolic rate | kcal |
| BMI, Visceral fat, Body age | computed on the scale |

## The profile matters

The scale has no impedance output. It computes body composition **itself**, from
a gender/age/height profile pushed to it at the start of every session, and
returns only the finished numbers. So:

- **Weight is always correct**, whatever the profile says.
- **Everything else is only as correct as the profile** you configure.

You can edit the profile any time via the integration's *Configure* button; the
entry reloads so the next weigh-in uses it.

The profile also carries a **slot** (P0, P1, …) — the same user selector the
scale's own display and the vendor app use. Set it to the slot you normally
weigh under so Home Assistant and the scale agree about whose history a reading
belongs to.

**Multi-person households** are handled with one *person* per config subentry
(Settings → the integration → **Add person**), each bound to a Home Assistant
user and to an on-scale profile slot (P0, P1, …). Home Assistant already
restricts config and subentry flows to admins, so only an admin can create or
change those associations.

Identification is by weight. At the start of a session the integration pushes
the profile of whoever weighed last — households repeat, so this is usually
right — then attributes the settled reading to whoever's known weight is
nearest, learning that weight for next time.

When the guess was right, the scale's composition figures are correct and kept.
When it was wrong, they were computed for somebody else's body, so they are
dropped rather than shown under the wrong name: **weight survives, and BMI is
recomputed** for the right height. The weight sensor carries
`body_composition_valid` and `match_margin_kg` attributes so an uncertain
attribution is visible instead of looking confident.

Use the `pcpw3008.reassign_measurement` service to move a weigh-in to the right
person. Same rule applies — weight and BMI move, composition does not.

## How it works

The scale is powered down and invisible almost all the time. Rather than poll,
the integration asks Home Assistant's Bluetooth stack to notify it when the
scale starts advertising — which happens when you step on it — and then runs one
short session:

```
connect                        ~2.7s once awake
subscribe 0xFFB2
write user profile     -> ACK
write unit             -> ACK, sometimes nothing at all
... live frames while you settle ...
final frame                    ~15s after connect
write measurement-done         -> scale powers off
```

Two behaviours worth knowing, both observed on real hardware:

- The **unit ACK is optional** — on some sessions the scale skips it and streams
  immediately, so the handshake is fire-and-forget rather than a state machine
  that waits.
- The **final frame repeats** several times, so measurements are de-duplicated.

Sensors deliberately stay `available` between weigh-ins. Tying availability to
the radio would make every entity unavailable most of the day and shred history.

## Requirements

- A **connectable** Bluetooth adapter or proxy within range of the scale. A
  passive-only ESPHome `bluetooth_proxy` can see the scale but never connect to
  it; it needs `active: true`.
- Nothing else — no account, no internet.

## Installation

**HACS** → ⋮ → *Custom repositories* → add this repo as an *Integration* →
install → restart Home Assistant.

**Manual** — copy `custom_components/pcpw3008/` into your `config/custom_components/`
and restart.

Then step on the scale: it should be discovered automatically. If not, add it
via *Settings → Devices & Services → Add Integration* **while the scale is
awake** — it cannot be found while asleep.

## Protocol

Documented in `custom_components/pcpw3008/protocol.py`, which is pure functions
and covered by `tests/test_protocol.py` against frames captured from real
hardware. Cross-checked against the vendor's "Dr.Curve+" app and openScale's
`HoffenBbs8107Handler`.

## Credits

Protocol work grew out of adding this scale to
[openScale](https://github.com/oliexdev/openScale) (PR #1476).

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

**Multi-person households are still not properly solved.** The slot is a label,
not an identity: the composition figures come from the gender/age/height pushed
in that same session, and Home Assistant has no way to know who actually stepped
on. Because the maths happens on-device there is no raw impedance to re-derive
from afterwards either, so a second person gets their own weight alongside the
configured profile's body composition. One profile per config entry is the
current model.

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

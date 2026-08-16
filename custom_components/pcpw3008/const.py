"""Constants for the ProfiCare PC-PW 3008 BT integration."""

from __future__ import annotations

DOMAIN = "pcpw3008"

# The scale advertises this exact local name. Matching on its advertised
# service 0xFEE7 instead would be wrong: that is the generic WeChat BLE
# service and plenty of unrelated hardware announces it.
LOCAL_NAME = "PC-PW 3008 BT"

# Advertised in the *primary* advertisement, so these survive passive scanning.
# The local name does NOT: the full record is 32 bytes, over the 31-byte legacy
# limit, so the name rides in the scan response and only an active scan sees it.
ADV_SERVICE_UUID = "0000fee7-0000-1000-8000-00805f9b34fb"  # generic WeChat service
MANUFACTURER_ID = 0x0131                                    # value is the MAC, reversed
MANUFACTURER_DATA_LEN = 6

SERVICE_UUID = "0000ffb0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffb2-0000-1000-8000-00805f9b34fb"

CONF_MALE = "male"
CONF_AGE = "age"
CONF_HEIGHT = "height_cm"
# On-scale user profile slot (P0, P1, ...). The scale keeps its own per-slot
# history, so matching it keeps Home Assistant and the scale display in step.
CONF_SLOT = "slot"
CONF_USER_ID = "user_id"
CONF_NAME = "name"
CONF_EXPECTED_WEIGHT = "expected_weight"

# One subentry per household member, each bound to an on-scale profile slot.
# Home Assistant gates config and subentry flows to admins already, so the
# "only admins may associate a person" rule needs no code of our own.
SUBENTRY_PERSON = "person"

DEFAULT_AGE = 40
DEFAULT_HEIGHT = 175
DEFAULT_SLOT = 0
MAX_SLOT = 7

# How long the config flow watches for the scale to advertise. It has to be
# long enough for someone to walk over and step on it, not just long enough
# for a radio scan.
DISCOVERY_TIMEOUT = 60.0

# How long to keep the link open waiting for the user to settle. The observed
# gap between connecting and the final frame is ~13-15s.
#
# Keep this tight. A session holds a lock that makes every advertisement
# arriving meanwhile a no-op, so an over-long timeout after a missed weigh-in
# swallows everybody else's attempts — three people stepping on in turn would
# all be ignored while one dead session ran out the clock.
SESSION_TIMEOUT = 30.0

# The scale re-sends the final frame several times. Ignore repeats of an
# identical measurement seen within this window.
DEDUPE_WINDOW = 30.0

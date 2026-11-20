# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) working in this repository. `CLAUDE.md` is a one-line `@AGENTS.md` import so Claude Code loads it too. Edit this file, not that one.

## What this repo is

A **self-contained Home Assistant custom integration** (Marketplace / HACS, category `integration`, domain `pcpw3008`) for the **ProfiCare PC-PW 3008 BT** Bluetooth LE bathroom scale. Everything shippable lives under [custom_components/pcpw3008/](custom_components/pcpw3008/); Home Assistant imports those files as they are — there is no build step and no third-party runtime requirement (`requirements` in the manifest is empty and should stay that way; `bleak`, `bleak_retry_connector` and `voluptuous` come with Home Assistant).

The firmware is Chipsea's "WeChat scale" platform, also sold as the Hoffen BBS-8107, so other rebrands may work. Only the PC-PW 3008 BT has been run against this code.

Platform: `sensor` only — nine sensors per person (weight, body fat, water, muscle, bone mass, BMR, BMI, visceral fat, body age).

## Repositories and remotes

Git `origin` is the self-hosted Forgejo repository (`source.tiagoagueda.com/tiagoagueda/hass-pcpw3008`) and is **canonical**; it is the only remote — do not add a `github` remote. `github.com/tiagoagueda/hass-pcpw3008` is a mirror that Forgejo pushes to, and exists only because the Marketplace installs from GitHub. Issues and PRs belong on Forgejo; anything pushed only to GitHub is overwritten.

`documentation` and `issue_tracker` in [manifest.json](custom_components/pcpw3008/manifest.json) point at Forgejo on purpose. Install instructions in the README must use the **GitHub** URL, because the Marketplace cannot read Forgejo.

The mirror copies git refs only. Repository description, topics and issue labels are set on each forge separately — keep them in step when you change one.

## Testing

```
python -m pytest tests/ -q
```

The tests cover the pure modules only — [protocol.py](custom_components/pcpw3008/protocol.py) against frames captured from a real scale, and [person.py](custom_components/pcpw3008/person.py) attribution rules. They need `pytest` and **not** Home Assistant: [tests/test_person.py](tests/test_person.py) registers a stand-in `pcpw3008` package so the relative imports resolve without executing `__init__.py`. Keep both modules free of Home Assistant imports so this keeps working.

There is no CI, linter or formatter configured. Code is written `ruff`-style (88 columns, double quotes) by convention.

Changes to `__init__.py`, `coordinator.py`, `config_flow.py` or `sensor.py` are not covered by the repo tests. When you touch them, run them against a real Home Assistant core with `pytest-homeassistant-custom-component` (set up the entry with person subentries, patch `coordinator.bluetooth.async_register_callback`, feed a captured frame through `ScaleCoordinator._handle_frame`) and check the log for `report_usage` deprecation warnings. Home Assistant core no longer imports on Windows (it needs `fcntl` and `resource`), so do this on Linux or WSL.

## Architecture (`custom_components/pcpw3008/`)

- [protocol.py](custom_components/pcpw3008/protocol.py) — pure framing: `0xFA`-prefixed frames with an XOR checksum, `iter_frames` (one notification can carry several frames), parsers for live (`RESP_LIVE`) and final (`RESP_FINAL`) frames, and builders for the user-profile, unit and measurement-done writes. The live frame states the weight divisor; trust it over a default.
- [coordinator.py](custom_components/pcpw3008/coordinator.py) — `ScaleCoordinator`, a `DataUpdateCoordinator` that is **never polled**: data is pushed with `async_set_updated_data`, keyed by person subentry id. It registers a Bluetooth callback on the scale's address; each advertisement (somebody stepped on the scale) starts one session under `_session_lock`.
- [person.py](custom_components/pcpw3008/person.py) — pure attribution: `match_by_weight`, `margin`, `attribute`, `learn_weight`.
- [sensor.py](custom_components/pcpw3008/sensor.py) — one device per person (identifier `<address>_<subentry_id>`), `RestoreSensor` entities with unique id `<address>_<subentry_id>_<key>`.
- [config_flow.py](custom_components/pcpw3008/config_flow.py) — config flow, `PersonSubentryFlow` (add / reconfigure a person), and an options flow.
- [__init__.py](custom_components/pcpw3008/__init__.py) — setup, the legacy migration, the reload listener and the `reassign_measurement` service.

### Session behaviour that must survive refactors

All observed on real hardware:

- The scale is asleep and invisible almost all the time. Never poll and never tie entity availability to the radio — sensors stay `available` with their last (restored) value between weigh-ins.
- Session: connect (~2.7 s) → subscribe `0xFFB2` → write profile → write unit → live frames → final frame (~15 s after connect) → write measurement-done, which powers the scale off.
- The **unit ACK is optional**, so the handshake is fire-and-forget and the frames drive everything. Do not turn it into a state machine that waits for ACKs.
- The **final frame repeats**; `_is_duplicate` drops repeats within `DEDUPE_WINDOW`.
- Keep `SESSION_TIMEOUT` tight. While a session holds the lock every new advertisement is ignored, so a long timeout after a missed weigh-in swallows the next people's weigh-ins.
- The scale's local name is in the **scan response**, so passively scanning adapters never see it. `is_scale` falls back to service UUID `0xFEE7` plus the 6-byte manufacturer record for id `0x0131`. `0xFEE7` alone is the generic WeChat service and matches unrelated devices.

### Multi-user rules

The scale computes body composition **on the device**, from the profile pushed at the start of the session, before anyone stands on it. So:

- The coordinator pushes the profile of whoever weighed last, then attributes the settled weight to whoever's learned weight is nearest.
- If that person's profile was pushed, the scale's figures are kept. Otherwise composition is **dropped**, weight is kept and BMI is recomputed for the right height. Never carry composition over to a different person, here or in `reassign_measurement`: it was computed for someone else's body.
- `match_by_weight` deliberately has no cut-off: a reading always lands on somebody and can be reassigned. A dropped weigh-in is worse.
- People are config **subentries** (`subentry_type` `person`). Home Assistant already restricts config and subentry flows to admins, which is the intended access rule — there is no permission code of our own.

### Reloads

After each weigh-in `_remember` writes the person's learned weight back to their subentry, and that write fires the entry's update listener. `_async_reload` compares `_config_signature(entry)` — all data, options and subentries **except** `expected_weight` — with the one taken at setup, and only reloads when it differs. Do not simplify this back into an unconditional reload: a reload throws away the coordinator's in-memory measurement and `last_attribution`, which made `reassign_measurement` fail after every weigh-in. If you add a value the coordinator writes back at runtime, leave it out of the signature too.

### Migrations

Early versions stored one profile on the config entry itself. `_ensure_person_subentry` turns it into the first person subentry, and `_migrate_legacy_unique_ids` re-points the old `<address>_<key>` entities at `<address>_<subentry>_<key>` so they keep their entity ids and history. `er.async_migrate_entries` is a coroutine — it must be awaited. Up to 0.4.3 it was not, so that migration silently did nothing.

The options flow still edits the entry-level profile, but since multi-user that profile is only read by the legacy migration. Per-person profiles are edited through the person subentry's *Reconfigure*.

## Home Assistant compatibility

- **Minimum: 2025.3.0**, set by config subentries (`ConfigSubentryFlow`, `async_add_entities(..., config_subentry_id=...)`, `_get_reconfigure_subentry`); 2025.2 has none of them. It is declared in [hacs.json](hacs.json) (`homeassistant`), which is what the Marketplace enforces. Raise it if you adopt a newer API.
- Tested against **2026.10.4** and **2026.11.2** (latest as of November 2026).
- Pass `config_entry=` to `DataUpdateCoordinator`. Do not use `DeviceInfo(via_device=...)` — it is deprecated and removed in 2027.8 (`via_device_id` replaces it), and the parent device it used to name was never created anyway.
- `DeviceInfo` is a dataclass since 2026.11 — construct it with keyword arguments only, never `info["key"] = ...`.
- Since 2026.9 `import voluptuous` is aliased to Home Assistant's Probatio engine. Keep importing `voluptuous`, never declare it as a requirement.
- Since 2026.12, calling `async_update_reload_and_abort` / `_abort_if_unique_id_configured(updates=...)` on an entry that has an update listener is an error. This integration has an update listener, so use `async_update_and_abort` / `async_create_entry` and let the listener decide about reloading.

## Marketplace (formerly HACS)

From Home Assistant 2026.11 HACS is built into core as the **Marketplace** (*Settings → Marketplace*); standalone HACS cannot be installed on 2026.11+, while older versions keep using HACS. Both read the same root [hacs.json](hacs.json) and the same `custom_components/<domain>/` layout, and both install from GitHub only.

- `hacs.json` keys the Marketplace understands: `name`, `content_in_root`, `filename`, `zip_release`, `persistent_directory`, `homeassistant`, `hide_default_branch`, `hacs`, `country`. Anything else is silently ignored (`render_readme` was removed for that reason).
- `manifest.json` must keep `domain`, `name`, `codeowners`, `documentation`, `issue_tracker` and a `version` that matches the release tag. `categories` (`health_wellbeing`) is optional and only feeds Marketplace browsing.
- Updates come from **GitHub releases**; a tag alone is not enough. Without releases users get the default branch and a commit hash.
- Being listed in the default catalog (`hacs/default`) is not required for custom-repository installs. If you go for it, it additionally needs the `hacs/action` (category `integration`) and `home-assistant/actions/hassfest` GitHub Actions passing with no ignored checks, issues enabled, and a description and topics on the GitHub repo.

## Branding

[custom_components/pcpw3008/brand/](custom_components/pcpw3008/brand/) holds `icon.png`, `icon@2x.png`, `dark_icon.png` and `dark_icon@2x.png`. Since HA 2026.3 a custom integration's `brand/` folder is served by the brands proxy and takes priority over the brands CDN, which has no `pcpw3008` entry, so this path is load-bearing — do not move it. The icon is a temporary placeholder generated by [scripts/make_brand_icon.py](scripts/make_brand_icon.py); never use Home Assistant branding in it.

## Translations

Custom integrations are not processed by Core's translation build, so [strings.json](custom_components/pcpw3008/strings.json) and [translations/en.json](custom_components/pcpw3008/translations/en.json) are both shipped and are currently **identical**. Edit both, with literal English and no `[%key:…%]` references.

## Versioning and releases

The only version number is `version` in [manifest.json](custom_components/pcpw3008/manifest.json). To release:

1. Bump `version`, commit, and tag `vX.Y.Z` with the same number.
2. `git push origin main --tags` — Forgejo pushes the commit and tag on to GitHub.
3. Once the tag is on GitHub, publish a release there: `gh release create vX.Y.Z -R tiagoagueda/hass-pcpw3008 --title "vX.Y.Z — <summary>" --notes "<notes>"`.

There is no CHANGELOG file; release notes live in the GitHub release.

## Conventions

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `diag:`, `chore:`).
- Comments and docstrings explain *why*, especially hardware behaviour seen in captures. Keep that habit, and keep the README's "How it works" section in step with the code.
- Do not assert hardware behaviour you have not observed. New protocol claims need a capture behind them, ideally added as a test frame.

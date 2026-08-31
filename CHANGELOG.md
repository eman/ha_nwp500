# Changelog

## [Unreleased]

## [0.20.0] - 2026-08-30

### Fixed
- **The reservation schedule is read at setup instead of merely requested.**
  Setup published `request_reservations` and moved on, so a reply that never
  arrived was indistinguishable from one that did -- and nothing retried
  until the periodic refresh, roughly twenty minutes later, leaving the
  Reservation Schedule sensor `unknown` for that whole window. Observed in
  the wild rather than theorised: the request went out 2.2s into setup, the
  device answered on its MQTT topic, and the subscription callback never
  ran. Setup now waits for the reply and asks again once if it does not
  come, and the periodic refresh waits and retries on the same terms so a
  dropped reply there costs another request rather than a full cycle of
  stale state. Because waiting has a cost, the setup reads run for every
  device at once rather than device by device -- otherwise silent devices
  would multiply the wait by the size of the account -- and the periodic
  refresh runs as a task per device instead of inside the status polling
  loop, where every device reaching the refresh cycle on the same update
  could have stalled status polling past its own interval. Best-effort
  throughout: a device that never answers is still left to the next
  refresh and never fails setup.
- **Reconfigure now saves the new password.** The reconfigure step called
  `_abort_if_unique_id_configured()`, but in a reconfigure flow the entry
  matching that unique ID *is* the entry being reconfigured -- so the flow
  aborted with "already_configured" before the credentials were written,
  and the one thing it exists to do, updating a changed Navien password,
  silently did nothing. It also merged a stray `title` key into the entry's
  data. Replaced with `_abort_if_unique_id_mismatch()`, which is the helper
  meant for reauth and reconfigure, and covered by tests that drive the real
  flow machinery rather than mocking the abort helper away.
- **Reauth no longer accepts a different account.** `reauth_confirm` never
  checked the submitted email against the entry's unique ID, so signing in
  with another Navien account rebound the entry to it while every device,
  entity and MQTT subscription stayed keyed to MACs from the original
  account. It now aborts with `wrong_account`.
- **Wrong passwords are reported as wrong passwords.** `validate_input`
  decided a failure was an auth failure by looking for "401" or
  "unauthorized" in the exception's text. It now catches the library's
  typed exceptions: `InvalidCredentialsError` is the only one carrying the
  invalid-login contract and maps to `invalid_auth`, while a bare
  `AuthenticationError` -- which the library also raises for non-401
  service errors and unparseable responses -- maps to `cannot_connect`, so
  a Navien outage no longer tells the user to change a working password.
- **Rejected credentials now lead to a reauth prompt.** `force_reconnect`
  retried indefinitely. When the cause was a revoked or changed password
  every attempt failed identically, so it spun at the 60s backoff cap
  forever -- logging warnings, never escalating, never telling the user what
  to fix. It now gives up after three consecutive credential rejections and
  reports through the same path the library's own reconnect loop uses,
  which starts the reauth flow. Only a rejected login escalates, and only
  `InvalidCredentialsError` means that: nwp500-python raises it for a 401
  or an "invalid"/"unauthorized" message, and turns every other non-200
  from that same response -- along with unparseable responses and internal
  state errors -- into a bare `AuthenticationError` that also defaults to
  `retriable=False`. Outages, brokers refusing connections, AWS SDK errors
  and missing-token conditions all keep retrying on the existing backoff
  instead of prompting the user to replace credentials that are valid.
- **Out-of-range water heater setpoints are reported.** `async_set_temperature`
  logged and returned, leaving the user looking at a temperature they
  believed they had changed. It now raises `ServiceValidationError`. Its
  docstring also described a library call the code does not make.
- **Empty energy usage reports work again after a timeout.** Once any
  `get_energy_usage` request timed out, the flag guarding against a late
  reply latched on for the life of the coordinator, so every genuinely empty
  period reported as a timeout ever after. The guard is now a bounded
  window rather than a latch. It is deliberately *not* cleared by the next
  matching reply: that reply is not necessarily the outstanding one, so
  doing that let a late empty reply -- which, carrying no months, matches
  every request -- be accepted as some third request's answer, presenting
  an all-zero report as measured data.
- **Diagnostics no longer mangle non-MAC identifiers.** The bare 12-hex
  branch of the MAC pattern had no boundaries, so it matched any 12
  characters of a longer hex or numeric run -- chewing digits out of epoch
  timestamps and splitting long identifiers into "**REDACTED**" fragments.
  The pattern is now fenced by hex-character boundaries.
- **The TOU schedule is read at setup.** Setup requested the initial
  reservation schedule but never the TOU plan, which is only re-read every
  40 update cycles -- so the TOU Schedule sensor read `unknown` for roughly
  the first twenty minutes after every restart while its reservation
  counterpart was populated immediately. The plan is now read once during
  setup. It is a REST read keyed by the controller serial number that only
  the MQTT device-info response publishes, so a device that has not
  reported yet is skipped quietly and left to the periodic refresh rather
  than logged as a failure.
- **Diagnostics keep their connection detail.** The location PII keys were
  redacted through the global key set, which matches at any depth, so a
  generic name like `state` blanked unrelated fields. Location redaction is
  now scoped to the location block itself.

- **`release.sh` inserted the version heading into prose.** Its CHANGELOG
  substitution matched `## [Unreleased]` anywhere on any line, not just the
  heading, so cutting a release rewrote the places this file *quotes* that
  string while describing the release tooling -- dropping a version heading
  into the middle of a sentence, and into a code span running across two
  lines. Both substitutions are now anchored to a whole line, and the
  heading one applies only to the first match. Covered by tests that run
  the real script against a throwaway repository.
- **`update_nwp500_version.py` wrote its CHANGELOG entry above the title.**
  With an empty `## [Unreleased]` section it fell back to
  `content.replace(section, ...)` with `section` empty, which inserts at
  offset 0 -- so the upgrade bullet landed before `# Changelog`. The
  section is now rewritten by span.

### Changed
- **Every temperature-bearing call is serialized against a unit system
  change.** Only `set_reservation` checked; `update_reservations` and the
  water heater's `set_temperature` did not, despite carrying temperatures
  that could be read in the wrong scale mid-transition -- nor did the
  target-temperature Number entity, which issues the same `set_temperature`
  command. All four now hold
  the coordinator's new `unit_transition_guard` across validation *and*
  dispatch, rather than reaching into a private attribute with `getattr`
  and then yielding. Checking a flag first was not sufficient for the
  water heater: the conversion to device units happens inside the library,
  after the publish has awaited, so a transition starting in between would
  encode a setpoint validated in one scale using the other. The guard holds
  `_unit_system_lock`, which `_atomic_unit_system_change` also requires, so
  a transition cannot interleave.
- **The bundled Lovelace cards escape interpolated values.** Entity names,
  states, units and card configuration were written straight into
  `innerHTML`. All such values now go through an `esc()` helper. The
  schedule card's `pad()` additionally validates: it formats the
  device-supplied reservation `hour` and `min`, whose result lands inside a
  quoted `title` attribute, so a malformed entry could otherwise have
  broken out of the attribute. It now coerces through `Number` and renders
  `--` for anything non-finite, which cannot carry markup at any call
  site.

- **Library Dependency: nwp500-python**: Upgraded to 9.3.1
- **The `request_tou_settings` service now reads the plan over REST.**
  nwp500-python 9.3.1 removed `NavienMqttClient.request_tou_settings()`:
  the device has no MQTT read for its TOU schedule -- `ctrl/tou/rd` is the
  *write*, and the device answers on `res/tou/rd` only to confirm one -- so
  the request this integration published never produced a schedule and, on
  9.3.1, would have raised `AttributeError`. The service now reads the
  stored plan from the cloud with `get_tou_info()` and publishes it exactly
  as a device reply was published: into the coordinator's `tou_schedules`
  and onto the bus as `nwp500_tou_updated`. The TOU Schedule sensor and
  anything listening for that event are unaffected -- the REST plan is
  flattened into the same shape the MQTT write confirmation arrives in --
  and the periodic schedule refresh now actually populates it. The read
  itself is pure REST, but it is keyed by the controller serial number,
  which only the MQTT device-info response publishes: it therefore still
  needs the device to have been heard from at least once since Home
  Assistant started. Writes (`configure_tou_schedule`) and the TOU switch
  are unchanged.

### Added
- **The device's last recorded fault is now readable while it is offline.**
  `/device/list` returns an `error` block that nwp500-python 9.3.1 models
  for the first time. The cloud keeps it independently of the live MQTT
  status, so the new **Last Reported Error** diagnostic sensor still
  reports the fault -- and, in its `occurred_at` attribute, when it
  happened -- at times when the existing Error Code sensor, which follows
  the MQTT status, has gone unavailable. Its availability deliberately
  follows the coordinator rather than MQTT staleness, so it survives the
  device going offline. A code the library's enum does not know is reported
  as its number rather than breaking the sensor.
- **Descaling window sensors** (**Descaling Start** / **Descaling End**),
  from the `descaling` block the same response carries. Both ends are unset
  on a device with no descaling scheduled or recorded, which is the common
  case, so these are disabled by default.
- **`model_type_code` and `installer_id`.** `deviceInfo` gained both in the
  cloud API and 9.3.1 models them. `model_type_code` is exposed as an entity
  attribute; both appear in a diagnostics dump, with `installer_id` redacted
  as it identifies another party and omitted entirely when the cloud did not
  populate it. Entity attributes now read the device object the coordinator
  currently holds rather than the one captured at platform setup, so this
  and `connected` follow the periodic refresh instead of staying frozen.
- **The device card now shows a model identifier.** Home Assistant renders
  `model_id` beside the model name, and it was never set. It is filled from
  the `model_type_code` the device reports over MQTT -- `NPF` on a heat pump
  water heater -- with an unrecognised code shown as its number. (The
  identically named REST field 9.3.1 adds is not used for this: the cloud
  returns it as null, and the device's own report is both populated and
  already available.)
- The device list is re-read every 20 coordinator cycles (~10 minutes at the
  default scan interval) so this cloud-side metadata stays current. It was
  previously fetched once, at setup. The re-read refreshes the devices
  already known, by MAC, rather than adopting the listing: entities and MQTT
  subscriptions are created once at setup and keyed to the devices known
  then, so a device the cloud momentarily omitted must not vanish, and a
  newly registered one cannot be adopted without a reload. A failed, empty
  or malformed re-read changes nothing.

- **On-demand energy usage report: the `nwp500.get_energy_usage` service.**
  The device keeps daily energy totals split between the heat pump and the
  resistive elements -- the split that matters, since element usage costs
  roughly three times as much per unit of heat -- and reports them only
  when asked. The service asks, waits for the reply, and returns it to the
  caller as a report; nothing is recorded and no entity is created, so it
  costs nothing when not in use. Defaults to the current month, accepts a
  year and several months at once, and date-stamps each day (the protocol
  numbers days only by their position in the month's list). The device
  answers on a topic keyed by MQTT client rather than by device -- the
  reply identifies neither the device nor the request -- so reports are
  served one at a time and a reply is accepted only while a request for
  that period is outstanding. A reply that arrives after its own request
  gave up is discarded rather than handed to whoever asked next.

## [0.19.0] - 2026-08-22

### Fixed
- **Three CI jobs were passing without running anything.** The `[testenv]`
  section in `tox.ini` had no `commands`, so `tox -e py314`, `tox -e mypy` and
  `tox -e basedpyright` installed their dependencies and exited successfully
  having executed no test or type check. Tests only ever ran via the separate
  coverage job; the type checkers had never run at all. All three now have
  explicit commands.
- **Per-module mypy settings had never been applied.** They were written as
  `["tool.mypy-homeassistant.*"]`, which TOML parses as a quoted *top-level*
  key rather than a mypy section, so mypy silently ignored all nine of them.
  Rewritten as a single `[[tool.mypy.overrides]]` block. Enabling it (together
  with the fix above) surfaced 10 real type errors, now resolved.
- **A failed setup left a live MQTT session behind.** The coordinator's first
  refresh runs `_setup_clients()`, which may already have opened an auth
  session, connected MQTT and started periodic request tasks before failing.
  Home Assistant discards that coordinator and retries with a fresh one, so
  each retry stranded a connection. `async_setup_entry` now shuts the
  coordinator down before re-raising.
- **`.github/copilot-instructions.md` declared `Current Version: 9.0.0` while
  the manifest pinned 9.3.0** — three minor releases stale, and invisible to
  the new pin checker because the number was not package-qualified. The
  duplicate is removed in favour of pointing at the manifest, and the checker
  now documents the forms it can actually verify.
- `.github/CI.md` still described the deprecated-API job as running Python 3.13
  and did not mention the dependency-pin job at all.
- **Frontend asset checks no longer block the event loop.** `async_setup`
  stat-ed three bundled card files inline; they are now checked in a single
  executor job. Each asset is also registered independently, so a missing
  schedule card no longer suppresses the visual card and its image.
- **README.md advertised nwp500-python v9.2.1 while the pin was 9.3.0.** The
  update script listed `README.md` among the files it rewrites, but its
  patterns only matched `nwp500-python==X.Y.Z`, never the README's
  `[nwp500-python v9.2.1](...)` link form — so it reported "No changes" and
  the reference drifted a full release behind.
- **A mistyped version made the update script lie.** It took the old version
  as an argument, so a typo matched nothing, rewrote no files, exited 0, and
  still added a CHANGELOG entry announcing the upgrade. The current version
  is now read from `manifest.json` and cannot be passed in.
- **Both Dependabot ecosystems had been failing every week.** The `pip` entry
  pointed at `/custom_components/nwp500` expecting to read `manifest.json`,
  which Dependabot's pip ecosystem cannot parse (`dependency_file_not_found`);
  it now points at the root `requirements.txt`. The `docker` entry pointed at
  `/`, which holds no Dockerfile (`No Dockerfiles nor Kubernetes YAML found`);
  it now points at `/.devcontainer`.

### Changed
- **The coverage gate now measures the whole integration.** `coordinator.py`
  (the largest module) and `diagnostics.py` were excluded from coverage while
  the gate claimed 80%, and `except Exception` was excluded line-wise.
  `diagnostics.py` turned out to be at 100% — omitting it was pure loss.
- **Coordinators now live on `entry.runtime_data`** instead of
  `hass.data[DOMAIN][entry.entry_id]`, with a typed `NWP500ConfigEntry` alias.
  This drops the multi-entry bookkeeping the integration no longer needs given
  `single_config_entry: true`, including the `isinstance` scan in the service
  handler and the reference-counting guard around service teardown.
- **Service registration and teardown are driven by one `_SERVICES` table**,
  rather than twelve `async_register` calls mirrored by twelve
  `async_remove` calls that could drift apart.
- Coverage flags moved out of pytest's `addopts` and into tox's coverage env,
  so running a single test file no longer fails the coverage gate.
- **`requirements.txt` now holds only runtime pins, halving the pin sites.** It
  mirrors `manifest.json`; development tooling moved to PEP 735
  `[dependency-groups]` in `pyproject.toml`, the current standard location.
  `tox.ini` installs from those files instead of repeating the versions in
  four environments, so it carries no pins at all. Counting both packages,
  hand-maintained pin sites drop from 16 to 8 — the manifest, its
  `requirements.txt` mirror, and the two user-facing "install this" error
  strings.

  `[testenv:coverage-html]` was also re-declaring `commands_pre` identically
  to `[testenv]`, which it already inherits; removing the copy took two more
  pin sites with it.

  The groups mirror the tox environments rather than lumping everything into
  one, so each still installs exactly what it did: the test env resolves Home
  Assistant through `pytest-homeassistant-custom-component`, the type checkers
  resolve it directly. Verified across recreated environments — Home Assistant
  2026.8.3, nwp500-python 9.3.0, awscrt 0.36.1 and aiohttp 3.14.3 in every
  one, unchanged.

  `tox-uv` is now declared in the `dev` group instead of being installed ad
  hoc by the devcontainer, since `runner = uv-venv` in `tox.ini` requires it.
  A full local setup is `uv pip install -r requirements.txt --group dev`.

  The base test environment previously installed `awsiotsdk` with
  dependencies and `nwp500-python` with `--no-deps`. It now installs
  `-r requirements.txt` in one step, which matches how Home Assistant installs
  manifest requirements at runtime. Verified on a recreated environment: the
  resolved versions of `aiohttp`, `pydantic`, `awscrt` and `awsiotsdk` are
  unchanged, and all tests pass.
- `scripts/update_nwp500_version.py` now discovers files by scanning instead
  of from a hardcoded list, handles the README link form, and can bump
  `awsiotsdk`. Its CHANGELOG update was also searching the whole file, so it
  matched a bullet from a past release and silently changed nothing while
  reporting success; it is now scoped to `## [Unreleased]

## [0.19.0] - 2026-08-22`.
- Both CI jobs that execute `scripts/` ran on Python 3.13 while ruff formats
  that directory for 3.14. The existing script happened to still parse; a
  reformat would have broken it. Both jobs now run 3.14.
- **Removing the energy sensors dropped in nwp500-python 9.3.0 is now a config
  entry migration** (`async_migrate_entry`, minor version 1 -> 2) instead of a
  full entity-registry sweep on every single setup. Existing entries are swept
  once on upgrade and then stamped; new installations never run it. Entries
  written by a future major version are refused rather than modified.
- **Releases now fail if the git tag and `manifest.json` disagree.** HACS
  installs the manifest version, so a hand-cut tag could previously ship a
  build whose reported version did not match the release.
- HACS validation no longer runs twice on every branch push (`on: push` had no
  branch filter alongside `pull_request`).

### Added
- **Tests for the version tooling itself** (`tests/unit/test_version_tooling.py`),
  covering every recognised reference form, the historical-prose exclusion, the
  no-op and refusal paths, an `awsiotsdk`-only bump, and both CHANGELOG shapes.
  Spot-checked by mutation: dropping the "refusing to record an upgrade"
  guard, restoring the whole-file CHANGELOG search, and letting the rewriter
  touch bare prose each fail at least one test.
- **`scripts/check_dependency_pins.py`, run in CI, makes a missed version bump
  impossible.** `manifest.json` is now the declared single source of truth for
  pinned versions — it is what Home Assistant installs and what hassfest
  validates. The checker scans every tracked file and fails if any pin
  disagrees with it.

  It scans every file git does not ignore, tracked or not — listing only
  tracked files made the check pass locally and fail in CI for a file that had
  been created but not yet `git add`ed, which is the worst behaviour a guard
  can have. Because it scans rather than working from a list, a new file that
  pins a version is covered without touching the script, and it checks
  `awsiotsdk` as well — which had 7 pin sites and no tooling at all.
  Historical prose ("dropped in nwp500-python 9.3.0") records when something
  happened and is deliberately not compared against the current pin.

  `nwp500-python` was defined in 9 places and `awsiotsdk` in 7, kept in sync
  by a script with a hardcoded file list, a prose list in `DEVELOPMENT.md`,
  and a 12-step checklist in `.github/copilot-instructions.md` — four
  hand-maintained copies of the same knowledge, all free to drift. All three
  now point at the scan-based tooling instead of enumerating files.

- **Coordinator test coverage raised from 38% to 95%** (90 new tests). Once
  `coordinator.py` was no longer excluded from the coverage report, it was the
  least-tested module in the integration despite being the largest. The new
  tests cover the paths that decide user-visible behaviour:

  - Retriable vs non-retriable authentication failures. `nwp500-python` marks
    transient network failures retriable, and only non-retriable ones should
    start a reauth flow — otherwise a brief outage nags the user to
    re-authenticate.
  - `async_shutdown` on a half-built coordinator, which is exactly the path
    taken when a first refresh fails and the connection leak fix above runs.
  - The `async_fetch_reservations` waiter lifecycle, including timeout and
    cleanup. `set_reservation` does a read-modify-write against a full-list
    replacement, so a stale or empty read corrupts the schedule.
  - Stored-token restore: valid, expired, and corrupt token data.
  - Unit-system transitions clearing every cache that holds scaled values, so
    a water heater never mixes Celsius and Fahrenheit readings.
  - Device command routing failing closed on an unknown MAC or absent MQTT,
    and TOU commands refusing to send before a controller serial is known.

  Integration-wide coverage is now 85.6%, and the gate is raised from 70% to
  80%.

### Removed
- `validate_hacs.py` and its `tox -e hacs` environment. It was scaffolding for
  getting into HACS; the authoritative `hacs/action` now runs in CI, and
  nothing invoked the local script.
- `custom_components/nwp500/images/navien-icon.png`, which was referenced
  nowhere and shipped to every user.
- `uv.lock`, which was an empty stub — three lines declaring a version and a
  Python floor, locking zero packages — and `pyproject.toml`'s `[build-system]`
  table, which pointed setuptools at something never built (`skipsdist = True`,
  no `[project]`). Both implied this repository is a distributable package; it
  is a Home Assistant custom component that HA loads directly. `pyproject.toml`
  is now purely configuration, which is all it ever was.
- **`NWP500WaterHeater.is_on`**, which Home Assistant never read.
  `WaterHeaterEntity` has no `is_on` property and its `state` is `@final`,
  derived from `current_operation` — so there was no hook to attach it to. The
  power state it computed is already reported correctly in three places:
  `current_operation` maps `POWER_OFF` to `"off"`, `switch.<device>_power`
  implements the same check where `is_on` is real API, and the component-status
  fields it fell back on (`dhw_use`, `comp_use`, `heat_upper_use`,
  `heat_lower_use`) are each already binary sensors.

  Those fallbacks were also wrong on their own terms: they tested whether a
  heating component was *currently running*, which is activity rather than
  power state, so a powered-but-idle device would have reported `False`.

## [0.18.0] - 2026-08-05

### Changed
- **Upgraded `nwp500-python` from 9.2.1 to 9.3.0**, which corrected a unit-scale
  error in the tank energy fields and renamed them to match what they actually
  measure. Both old fields were removed outright rather than aliased, so the
  entities built on them are replaced:

  | Removed entity | Replacement | Meaning |
  | --- | --- | --- |
  | Total Energy Capacity | Full Recovery Energy | Energy to recover a fully depleted tank to the current setpoint |
  | Available Energy Capacity | Energy to Setpoint | Energy needed to bring the tank from its current temperature up to the setpoint |
  | — | Usable Energy *(new, enabled by default)* | Energy drawable from the tank as useful hot water |

  **Reported values are now 2.5x smaller.** The previous numbers were wrong, not
  the new ones. Any history, statistics, or energy dashboard configuration built
  on the old entities is off by that factor; rescale it by 0.4 or discard it.

  Only Usable Energy behaves like a state of charge. The other two are both
  measured from the setpoint, so they move when the setpoint moves even though
  the water in the tank does not — which is why the `energy_storage` device class
  now applies to Usable Energy alone. Full Recovery Energy and Energy to Setpoint
  remain disabled by default.
- **Eight status flags can now report `Unknown` instead of `Off`**: `Operation
  Busy`, `Compressor Running`, `Anti-Legionella Enabled`, `Anti-Legionella Cycle
  Running`, `Upper Electric Heating Element`, `Lower Electric Heating Element`,
  `Air Filter Alarm Enabled`, and `Recirculation Reservation Active`. The device
  encodes these as unknown/off/on, and the library previously collapsed unknown
  to off. Automations that treat these as strictly on/off should account for the
  unknown state.

### Fixed
- **Stale energy entities are removed on upgrade**: the two entities whose
  backing device fields no longer exist are deleted from the entity registry at
  setup instead of lingering as permanently unavailable.

## [0.17.1] - 2026-07-30

### Fixed
- **MQTT failed to connect after Home Assistant upgraded the AWS SDK**:
  `manifest.json` requested `awsiotsdk>=1.29.0`, so Home Assistant installed
  whatever was newest. When `awsiotsdk` 1.31.0 was published (2026-07-24) it
  was installed *during* a Home Assistant startup, seconds before the
  integration connected. The process already held modules from the previous
  `awscrt`, so newly imported code read attributes the old classes did not
  define, and MQTT setup failed with:

      MQTT connection failed: 'ClientTlsContext' object has no attribute
      '_certificate_source'

  The integration then silently dropped to API-only mode and stayed there
  until Home Assistant was restarted. Pins `awsiotsdk==1.31.0`, which in turn
  pins `awscrt==0.36.1` exactly, so the AWS SDK now only changes when this
  integration changes it — never mid-session. No code changes were needed to
  adopt 1.31.0; the integration does not use the `awsiotsdk` API directly.
- **Unclear error when the AWS SDK is upgraded mid-session**: an
  `AttributeError` raised while connecting is now reported as what it
  actually is — a stale-module condition that only a restart clears — instead
  of surfacing a bare attribute error with no indication of what to do.
  Genuine connection failures keep their existing wording.
- **Release tooling documentation pointed at a tool that cannot run**:
  `.bumpversion.cfg` declared `current_version = 0.2.2` while the published
  version was `0.16.2`, so `bump2version` computed the wrong next version and
  then aborted with `VersionNotFoundException` because `"version": "0.2.2"`
  no longer appears in `manifest.json`. Two of its three file rules were
  broken independently of that: the `README.md` rule searched for a
  `**Version**:` line that does not exist, and the `CHANGELOG.md` rule
  renamed the previous release's heading instead of adding a new section —
  the likely cause of the corrupted `## [## [0.2.2]] - 2026-02-08 -
  2026-02-08` heading in this file. Meanwhile `DEVELOPMENT.md` presented
  `bump2version` as "the recommended way to release" and never mentioned
  `scripts/release.sh`, the script that actually performs releases. Removes
  `.bumpversion.cfg` and the unused `bump2version` dependency, and rewrites
  the release documentation around `scripts/release.sh`.
- **Library upgrade documentation described a manual checklist**: adopting a
  new `nwp500-python` version means editing eight files, and
  `scripts/update_nwp500_version.py` already automates all of them. The docs
  in `DEVELOPMENT.md` and `.github/copilot-instructions.md` listed the files
  to edit by hand without mentioning the script, which is how the 9.2.1
  adoption missed the install hint in `coordinator.py` and left the two
  runtime error paths naming different versions. Both now lead with the
  script. Also corrects a stale `[testenv:pyright]` reference to
  `[testenv:basedpyright]`.
- **Corrupted changelog headings**: repaired
  `## [## [0.2.2]] - 2026-02-08 - 2026-02-08`, which is the v0.2.0 release
  (published 2026-02-08), and removed a duplicated `## [0.15.4]` heading.

## [0.17.0] - 2026-07-30

### Added
- **Programmed schedules readable as entity state**: the reservation and TOU
  schedules were only reachable as in-process coordinator dicts and one-shot
  `nwp500_reservations_updated` / `nwp500_tou_updated` bus events, so an
  external scheduler had to run a WebSocket request/event dance and could not
  poll them like normal state. Adds two diagnostic sensors per device,
  **Reservation Schedule** and **TOU Schedule**, whose state is the number of
  programmed entries and whose attributes carry the program itself:
  - `entries` — the raw entries as the device reports them
  - `enabled` — whether the schedule system is switched on at the device
  - `schedule_hash` — a stable, order-independent hash of the program, so a
    consumer can check desired-vs-programmed with one comparison instead of
    diffing entries. Mirrors `ReservationSchedule.canonical()` in
    nwp500-python.

  The state is `None` until the schedule has been read, keeping "not fetched
  yet" distinct from "device has no entries". The schedules are also
  re-requested every `SCHEDULE_REFRESH_CYCLES` (40) coordinator updates —
  roughly every 20 minutes at the default interval — so the exposed state
  reflects changes made outside Home Assistant, in addition to the existing
  refresh after each write. Implements
  [issue #103](https://github.com/eman/ha_nwp500/issues/103).

### Fixed
- **Reservation and vacation service documentation corrected**: three
  descriptions contradicted the code or the library. The `update_reservations`
  `reservations` field documented `enable (1=enabled, 2=disabled)`, which is
  inverted — the device bool convention is `2=on, 1=off`
  (`ReservationEntry.enabled` is `enable == 2`), as the service's own
  validation message already said. `set_reservation` claimed the device
  supports "up to 7 reservation entries" while the library documents ~16.
  `set_vacation_days` described a 1-365 day range while its own field
  description, selector and validator all cap at 30, which is what the library
  enforces. Also synchronized `services.yaml` with `strings.json` and
  `translations/en.json`, which had drifted independently: six `entity_id`
  fields were missing from the strings files entirely, two service
  descriptions were truncated, and the TOU `periods` description had lost its
  field-by-field key list. Adds tests pinning the three files together so they
  cannot drift again. Fixes
  [issue #105](https://github.com/eman/ha_nwp500/issues/105).
- **`set_reservation` could wipe every reservation on the device**: the
  service does a read-modify-write against the cached reservation schedule,
  but the write is a full-list replacement at the protocol level. When the
  schedule had never been fetched the cache was empty, and the handler only
  logged a warning before pushing a single-entry list — replacing every
  reservation the device held. It now fetches the schedule and waits for the
  device's response first, and raises an error rather than writing if the
  schedule cannot be read. Adds `NWP500DataUpdateCoordinator.async_fetch_reservations()`,
  which requests the schedule and awaits the reply, since
  `async_request_reservations()` only publishes the request.
- **`set_reservation` was append-only despite its name**: documented as
  "create or update", the handler only appended, so repeating a call — or
  programming a different mode at a day and time already scheduled —
  accumulated conflicting entries with no way to update one in place. An
  entry occupying the same slot (same `week` bitfield, hour and minute) is
  now replaced.
- **`set_reservation` forced the device's reservation system on**: the
  handler passed a hardcoded `enabled=True` to the full-list write, so
  adding or updating a single entry silently re-enabled the schedule-wide
  reservation switch on a device where it had been turned off. That switch
  (`reservation_use`) is separate from this service's entry-level `enabled`
  field, and is now preserved from the device's own reported state. Fixes
  [issue #104](https://github.com/eman/ha_nwp500/issues/104).
- **`configure_tou_schedule` rejected Sunday and every-day periods**: the
  service validated each period's `week` bitfield with `Range(min=0, max=127)`,
  which excludes bit 7 (Sunday = 128) and therefore every Sunday-inclusive
  mask, including "every day" = 254. TOU uses the same weekday bitfield as
  reservations (`Sun=128..Sat=2`), and the reservation validator in the same
  file already allowed `0-254`. Raised the TOU bound to match, added the same
  explanatory message, and corrected the `services.yaml` field description,
  which also documented `0-127`. Fixes
  [issue #106](https://github.com/eman/ha_nwp500/issues/106).
- **CI: ruff 0.16.0 format check**: The `ruff` tox environment declared
  `ruff>=0.1.0`, so CI floated to whatever ruff had most recently released.
  ruff 0.16.0 began formatting fenced code blocks inside Markdown, which broke
  the `Lint (ruff)` job on every pull request: two ```` ```python ```` blocks in
  `scripts/README.md` are illustrative listings of deprecated API names with
  deliberately aligned trailing comments, not runnable code. Retags those two
  blocks as ```` ```text ```` so the formatter leaves the alignment alone, and
  raises the floor to `ruff>=0.16.0` so a local run cannot silently pass with an
  older ruff than CI uses.
- **Declared Home Assistant floor raised to 2026.3.0**: `hacs.json` declared
  `2025.1.0`, but the integration ships PEP 758 parenthesis-less multi-type
  `except` clauses (e.g. `except CannotConnect, InvalidAuth:`) in nine modules.
  That syntax is Python 3.14 only, which means Home Assistant 2026.3 or newer;
  on anything older every one of those modules fails to import with a
  `SyntaxError`, so users on 2025.x through 2026.2 would install a broken
  integration from HACS. Raises the declared floor to `2026.3.0` and corrects
  the contradictory "Requires Home Assistant 2025.1+ (Python 3.14)" module
  docstring, the `homeassistant>=2025.1.0` development pin, and the stale
  Python 3.12/3.13 references in the development and CI docs. Raised in
  [hacs/default#6988](https://github.com/hacs/default/pull/6988).
- **Dev container could not run the integration**: `.devcontainer/Dockerfile`
  built on `python:3.13`, so the "Recommended" development path could not
  import the integration's Python 3.14-only syntax at all. Bumped to
  `python:3.14` and corrected the dev container's stale
  `homeassistant>=2024.1.0` package list. Also aligned the `homeassistant`
  floor in the mypy/basedpyright tox environments with the declared HACS
  minimum, and corrected the CI/development docs, which described a
  `tox -e pyright` environment that does not exist (the env and CI job are
  both `basedpyright`) and listed a Python 3.12/3.13 job matrix that no longer
  matches `.github/workflows/ci.yml`.

### Changed
- **Library Dependency: nwp500-python**: Upgraded to
  [9.2.1](https://github.com/eman/nwp500-python/releases/tag/v9.2.1). No
  breaking changes affecting this integration, and no code changes were
  required. Notable changes:
  - Demand response commands are now gated on the `dr_setting_use` capability
    flag ([issue #114](https://github.com/eman/nwp500-python/issues/114)).
    The `nwp500.enable_demand_response` / `nwp500.disable_demand_response`
    services will now fail with "Failed to enable/disable demand response" on
    devices that do not report DR support, instead of silently dispatching a
    command the device ignores. This matches the behavior the integration
    already had for every other capability-gated command (`set_power`,
    `set_dhw_mode`, `set_tou_enabled`, recirculation), so no new handling was
    needed.
  - `set_freeze_protection_temperature` now validates against the device's
    reported `freeze_protection_temp_min`/`freeze_protection_temp_max` and is
    gated on the `freeze_protection_use` capability
    ([issue #112](https://github.com/eman/nwp500-python/issues/112)). This
    integration does not call that command, so there is no impact.
  - Adds opt-in `update_reservations_confirmed()` /
    `configure_tou_schedule_confirmed()` helpers that await the device's
    `rsv/rd`/`tou/rd` echo and return the schedule the device actually holds,
    plus `canonical()` comparison helpers
    ([issue #111](https://github.com/eman/nwp500-python/issues/111)). The
    `update_reservations` and `configure_tou_schedule` services still use the
    fire-and-forget variants and report success once the write is published;
    adopting the confirmed variants is tracked separately.
  - Documentation fix to the `decode_reservation_hex` docstring
    ([issue #113](https://github.com/eman/nwp500-python/issues/113)).
- **Removed unreachable AWS CRT error handling**: Since `nwp500-python` v9.2.0,
  `NavienMqttClient.publish()` no longer lets `awscrt` exceptions escape — a
  clean-session cancellation during reconnection is enqueued in the library's
  command queue and returns normally, and any other AWS CRT failure is wrapped
  in `MqttPublishError`. The integration's own
  `AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION` special-casing on the command
  path was therefore dead code and has been removed, along with the
  `get_aws_error_name()` helper. `MqttPublishError` is already covered by the
  existing `MqttError` handler. `AwsCrtError` handling is retained for the
  connect/disconnect path, where the library still propagates it. No functional
  change.

## [0.16.2] - 2026-07-14

### Fixed
- **MQTT reconnection retry loop**: Fixed a critical bug where
  `force_reconnect()` would fail to retry after a transient auth service or
  network error. Previously, when reconnection failed during the initial
  attempt, the method would return instead of rescheduling retries with
  exponential backoff. This caused the integration to become permanently stuck
  disconnected, requiring manual Home Assistant restart to recover. The fix
  converts `force_reconnect()` to loop internally with exponential backoff
  (2s, 5s, 15s, 30s, 60s cap), continuously retrying until successful
  reconnection or task cancellation. This ensures automatic recovery from
  transient auth/network failures. Fixes
  [issue #100](https://github.com/eman/ha_nwp500/issues/100).

## [0.16.1] - 2026-07-14

### Fixed
- **MQTT reconnection rate-limiting bug**: Fixed a critical bug in the
  reconnection rate-limit logic that prevented the integration from recovering
  after MQTT connection failures. When a reconnection attempt was rate-limited
  (< 30 seconds since the last attempt), the timeout counter was incorrectly
  reset to 0, preventing the reconnection threshold from ever being reached
  again. This caused the integration to remain disconnected indefinitely even
  when the MQTT service became available. The fix removes the counter reset
  from the rate-limit case, allowing the counter to accumulate normally so
  reconnection proceeds once the rate-limit interval expires. Fixes
  [issue #100](https://github.com/eman/ha_nwp500/issues/100).

## [0.16.0] - 2026-07-07

### Fixed
- **MQTT reconnect alignment with `nwp500-python` v9.0.0**: The coordinator no
  longer tears down the MQTT client after three disconnected update cycles while
  the library's own hardened internal reconnect loop is already running. Forced
  reconnect remains as a last-resort escape hatch only for repeated
  coordinator-level request timeouts on a connection that still appears up, and
  the integration now listens for the library's `reconnection_failed` event to
  trigger Home Assistant reauth when the internal loop stops permanently.
- **AWS CRT clean-session warning workaround removed**: `nwp500-python` v9.2.0
  fixes [issue #97](https://github.com/eman/nwp500-python/issues/97) upstream:
  `_await_ack()` and the subscribe/unsubscribe acknowledgement waits now
  attach a done callback whenever a shielded AWS CRT future is abandoned, so
  the eventual result/exception is always retrieved and logged at debug level
  by the library instead of leaking to asyncio's global exception handler.
  The coordinator's temporary global asyncio exception-handler
  install/restore machinery (`_nwp500_exception_handler`,
  `_install_exception_handler`, `_restore_exception_handler`, and the shared
  refcount globals) is no longer needed and has been removed.
- **Recirculation Active binary sensor**: Removed the redundant "Recirculation
  Active" binary sensor (`recirculation_use`), which read the same
  `DeviceStatus.recirc_operation_busy` field as the existing "Recirculation
  Operation Busy" sensor and duplicated it with a nonexistent `recirc_use`
  fallback. Use the existing "Recirculation Operation Busy" sensor instead.

### Changed
- **Internal API cleanup / Python 3.14 modernization**: Removed redundant
  `from __future__ import annotations` imports from all
  `custom_components/nwp500/` modules now that this integration is
  Python 3.14-only, updated stale `nwp500-python` version-pinned comments
  to reflect current library behavior, and replaced the auth-client
  shutdown dunder call with the library's public `close()` API. Initial
  auth setup still relies on `NavienAuthClient.__aenter__()` because
  `nwp500-python` does not yet expose a matching public connect/open
  lifecycle method for the coordinator's longer-lived auth session.
- **Library Dependency: nwp500-python**: Upgraded to
  [9.2.0](https://github.com/eman/nwp500-python/releases/tag/v9.2.0). No
  breaking changes affecting this integration. Notable changes:
  - Fixes [issue #97](https://github.com/eman/nwp500-python/issues/97):
    `_await_ack()` in `mqtt/connection.py` and the subscribe/unsubscribe
    acknowledgement waits in `mqtt/subscriptions.py` now attach a done
    callback whenever a shielded AWS CRT future is abandoned due to a
    timeout or cancellation, so its eventual result/exception is always
    retrieved and logged at debug level instead of leaking as an unhandled
    "Future exception was never retrieved" asyncio warning. This obsoletes
    this integration's own global asyncio exception-handler workaround,
    which has been removed (see "Fixed" above).
  - Internal refactors with no behavior change: CLI formatting stacks
    merged behind a single Rich renderer, `mqtt/client.py` slimmed via
    mixins, event-name constants de-duplicated between `events.py` and
    `mqtt_events.py`, and `awscrt` types (e.g. `mqtt.QoS`) wrapped behind a
    library-owned `nwp500.QoS` enum on public MQTT signatures. This
    integration does not use `awscrt.mqtt.QoS` directly, so no code changes
    were required.
- **Library Dependency: nwp500-python**: Upgraded to 9.0.0 (BREAKING). This
  is a major version bump on the library side that trims its public API
  surface and removes dead code; see the
  [full release notes](https://github.com/eman/nwp500-python/releases/tag/v9.0.0)
  for the complete list. Notable changes affecting this integration:
  - Removed the never-raised `TokenExpiredError` exception. The coordinator's
    authentication error handling no longer imports or catches it (the
    remaining `TokenRefreshError`/`AuthenticationError` handling is
    unaffected, since the library never actually raised this class).
  - Numerous bug fixes relevant to this integration's stability: queued
    control commands are now preserved in order and expire after a
    configurable age instead of replaying stale commands after long
    outages; MQTT message dispatch now runs on the event loop instead of
    the AWS CRT network thread, fixing a potential
    `RuntimeError: dictionary changed size during iteration` during
    reconnects; the reconnection loop now survives all library errors
    instead of dying silently on auth/token errors during an outage;
    `error_detected` events are now emitted when the error code changes
    between two non-zero values; and sub-zero Fahrenheit temperature
    conversions are now rounded correctly.
  - No changes were required to this integration's own use of
    `build_reservation_entry`/`build_tou_period`, since it already imports
    them from `nwp500.encoding` rather than the removed top-level
    re-exports.
- **Water heater mode enums**: Replaced duplicated magic-number protocol values
  with `nwp500.enums.CurrentOperationMode` / `DhwOperationSetting` members and
  consolidated DHW mode-to-state translation so the water heater entity and its
  extra state attributes share one base mapping while preserving the vacation
  restore behavior.

## [0.15.5] - 2026-06-15

### Changed
- **Library Dependency: nwp500-python**: Upgraded to 8.1.3. Fixes a
  thread-safety bug in `on_connection_resumed` where `Task.cancel()` was called
  directly from an AWS IoT SDK background thread. When the event loop was busy,
  the cancellation could be silently dropped, leaving a stale
  `_reconnect_with_backoff` task that would complete its sleep and tear down an
  otherwise healthy connection — restarting the
  disconnect → reconnect → `AWS_ERROR_MQTT_UNEXPECTED_HANGUP` cycle. See
  [nwp500-python PR #89](https://github.com/eman/nwp500-python/pull/89).

## [0.15.4] - 2026-06-05

### Fixed
- **MQTT reconnection recovery loop**: The `force_reconnect` method was updating `_last_reconnect_time` before attempting setup, which prevented the integration from retrying failed reconnections. If setup failed, the rate-limiting check in the coordinator would see the recent timestamp and block further attempts for 30 seconds, keeping the device stuck in a disconnected state. Now `_last_reconnect_time` is updated only after successful setup, allowing retries on failed attempts while still preventing excessive reconnect attempts on success.

## [0.15.3] - 2026-05-25

### Fixed
- **MQTT client ID conflict**: All HA instances authenticated with the same Navien account previously shared the same MQTT client ID (`navien-ha-{user_seq}`). AWS IoT Core only allows one active connection per client ID, so running more than one instance (e.g. production + local dev) caused a continuous ping-pong of disconnections. The client ID now incorporates the HA installation's unique instance UUID: `navien-ha-{user_seq}-{ha_uuid[:8]}`, making it stable across restarts and unique per installation.
- **MQTT subscriptions lost after auto-reconnect**: When the AWS IoT SDK reconnected internally before the integration's own reconnect logic fired, `session_present=False` was reported but no `resubscribe_all()` was called — leaving the client connected but with zero active topic subscriptions. Device status updates stopped flowing silently until a forced full reconnect occurred. Fix pending release in `nwp500-python` (see [fix/mqtt-resubscribe-on-session-lost](https://github.com/eman/nwp500-python/tree/fix/mqtt-resubscribe-on-session-lost)).
- **Persistent MQTT session**: Changed `clean_session` from `True` (library default) to `False`. Combined with the now-unique stable client ID, the AWS IoT broker resumes the existing session on reconnect (`session_present=True`), so subscriptions are preserved server-side and `AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION` errors are eliminated.

### Changed
- **Library Dependency: nwp500-python**: Upgraded to 8.1.2

## [0.15.2] - 2026-05-18

## [0.15.1] - 2026-05-16

### Changed
- **Library Dependency: nwp500-python**: Upgraded from v8.0.0 to v8.1.0.
  - **v8.1.0 (2026-05-16)**: Multiple bug fixes — see [release notes](https://github.com/eman/nwp500-python/releases/tag/v8.1.0)

## [0.15.0] - 2026-05-15

## [0.14.5] - 2026-05-13

### Changed
- **Library Dependency: nwp500-python**: Upgraded from v7.4.10 to v8.0.0 (stable release).
  - **v8.0.0 (2026-05-13)**: Official stable release — see [release notes](https://github.com/eman/nwp500-python/releases/tag/v8.0.0)
- **Library Dependency: awsiotsdk**: Upgraded to `>=1.29.0`.

## [0.14.4] - 2026-04-14

### Fixed
- **README**: Corrected stale version number (was showing 0.3.0, now reflects current version)

### Changed
- **CI**: Removed `ignore: brands` from HACS validation workflow — brand assets are now provided via `custom_components/nwp500/brand/` directory

## [0.14.3] - 2026-04-13

### Changed
- **Brand images**: Further refinements and quality improvements to `icon.png` and `logo.png`

## [0.14.2] - 2026-04-13

### Added
- **Brand images**: Added `brand/` directory with `icon.png` (256×256) and `logo.png` (1616×225 wordmark) for Home Assistant 2026.3+ local brand image support in custom components

### Changed
- **Library Dependency: nwp500-python**: Upgraded from 7.4.8 to 7.4.10
  - **7.4.10 (2026-04-13)**: Loosened `pydantic` requirement from `>=2.12.5` to `>=2.0.0` for Home Assistant compatibility
  - **7.4.9 (2026-04-13)**: Bug fixes — see [release notes](https://github.com/eman/nwp500-python/releases/tag/v7.4.9)
- **Tooling**: ruff, mypy, and pyright configs updated to target Python 3.14
- **CI**: Bumped `actions/upload-artifact` from 5 to 7, `codecov/codecov-action` from 5 to 6, `softprops/action-gh-release` from 2 to 3

### Fixed
- **Away mode toggle**: UI toggle now correctly activates vacation mode for 1 day via `set_vacation_days(days=1)` — previously called `set_dhw_mode(mode=5)` without the required `vacation_days` parameter, causing the command to be silently ignored by the device
- **Away mode heating mode display**: The UI no longer switches to `eco` when vacation mode is activated; the previous heating mode is preserved and restored when away mode is turned off
- **Away mode restore**: `async_turn_away_mode_off` validates the stored pre-vacation mode, warns and falls back to `eco` if unmapped, and clears state only after the restore call
- **MQTT callbacks**: `add_done_callback` lambdas now guard against `CancelledError` before inspecting `f.exception()`
- **Recirculation mode**: `set_recirculation_mode` now logs an error when the required `mode` kwarg is missing instead of silently no-opping
- **Blueprint fixes**: `nwp500_away_mode` — fixed person-list expansion, corrected `energy_saver` → `eco`, vacation days capped at 30; `nwp500_solar_boost` / `nwp500_demand_response` — minor value corrections
- **Service schema**: `set_vacation_days` range corrected to 1–30 days (was 1–365)

## [0.14.0] - 2026-04-13

### Added
- **5 new device control services**:
  - `enable_demand_response`: Enable participation in utility demand response programs
  - `disable_demand_response`: Disable utility demand response participation
  - `reset_air_filter`: Reset the air filter maintenance timer after cleaning/replacement
  - `set_recirculation_mode`: Set recirculation pump mode (Always On / Button / Schedule / Temperature Triggered) with a labeled dropdown UI
  - `trigger_recirculation`: Manually trigger the recirculation pump hot button
  - All new services support both `device_id` and `entity_id` target selection
- **4 new automation blueprints** (in `blueprints/automation/`):
  - `nwp500_solar_boost`: Switch to High Demand mode when solar generation exceeds a configurable threshold; revert to Eco when it drops
  - `nwp500_away_mode`: Activate vacation mode when all tracked household members leave; restore normal mode on return
  - `nwp500_leak_alert`: Send a notification and optionally power off the water heater when a moisture/flood sensor triggers
  - `nwp500_demand_response`: Enable/disable demand response via a binary sensor (utility signal) or a scheduled time window

### Changed
- **Library Dependency: nwp500-python**: Upgraded from 7.4.8 to 7.4.10
  - **7.4.10 (2026-04-13)**: Loosened `pydantic` requirement from `>=2.12.5` to `>=2.0.0` for Home Assistant compatibility
  - **7.4.9 (2026-04-13)**: Bug fixes and dependency updates
    - Fixed timezone-naive datetime in token expiry checks (uses `datetime.now(UTC)` throughout)
    - Fixed vacation mode sent wrong MQTT command (`set_vacation_days()` now uses correct `DHW_MODE` command; valid range corrected to 1–30 days)
    - Fixed duplicate AWS IoT subscribe calls on reconnect
    - Fixed anti-legionella set-period state preservation (no longer re-enables when feature is off)
    - Fixed subscription state lost after failed resubscription
    - Fixed unit system detection returning `None` on timeout
    - Fixed once-listener becoming permanent with duplicate callbacks
    - Fixed auth session leaked on client construction failure
    - Bumped minimum dependency versions: `aiohttp>=3.13.5`, `awsiotsdk>=1.28.2`
    - See [release notes](https://github.com/eman/nwp500-python/releases/tag/v7.4.9) for full details
- **Service schemas**: `set_vacation_days` and `configure_tou_schedule` now accept `entity_id` in addition to `device_id` (consistent with all other services)
- **Recirculation mode UI**: `set_recirculation_mode` service now shows a labelled select dropdown instead of a plain number field
- **MQTT command logging**: All `send_command()` dispatches now emit a unified debug log entry
- **Water heater refactor**: Extracted `_control_device()` helper in `water_heater.py`, eliminating repeated boilerplate across `set_temperature`, `set_operation_mode`, `turn_away_mode_on`, and `turn_off`

### Fixed
- **README version badge**: Updated from 0.2.2 to 0.3.0
- **Energy capacity sensor classification**: `total_energy_capacity` and `available_energy_capacity` corrected from `device_class=energy / state_class=total` to `device_class=energy_storage / state_class=measurement` — these represent current stored energy (fluctuates), not a cumulative counter
- **Coordinator null guard**: Added `if not coordinator.data:` check before iterating device keys during service resolution, preventing a crash when coordinator data is not yet populated
- **Sensor telemetry None guards**: MQTT request/response count sensors now use `.get()` with a fallback of 0 instead of direct dict access, preventing `KeyError` on uninitialized telemetry
- **Thread safety — connection interruptions**: `_connection_interruptions` changed from a plain `list` to `deque(maxlen=20)`, making it safe for MQTT callback threads and eliminating the manual truncation loop
- **Private attribute access**: `coordinator.py` now uses the public `mqtt_manager.last_reconnect_time` property instead of accessing `_last_reconnect_time` directly
- **Silent Future exceptions**: `asyncio.run_coroutine_threadsafe()` calls in MQTT callbacks now attach `.add_done_callback()` error handlers so exceptions are logged rather than silently discarded
- **MAC address tracking**: `_tracked_mac_addresses` changed from `list` to `set` for O(1) membership checks and to prevent duplicate subscriptions
- **Local import hoisting**: Repeated local imports of `UnitOfTemperature` and temperature constants inside function bodies moved to module level in `__init__.py`

## [0.3.0] - 2026-02-21

### Added
- **Custom Lovelace Cards**: Two bundled custom frontend cards served automatically by the integration
  - `nwp500-schedule-card`: Visual weekly schedule editor for managing heating reservations
  - `nwp500-visual-card`: Visual status card showing current device state and temperatures
  - Cards are registered as Lovelace resources automatically — no manual resource configuration needed
- **Time of Use (TOU) Services**: Added two new services for managing TOU schedules
  - `configure_tou_schedule`: Configure time-based rate periods (up to 16 periods)
  - `request_tou_settings`: Request current TOU configuration from device
- **Vacation Days Service**: Added `set_vacation_days` service to configure vacation mode duration
- **Entity ID support for services**: All services now accept either `device_id` or `entity_id` to identify the target device
- **MQTT subscription handling**: Enhanced coordinator to handle TOU/reservation response subscriptions

### Changed
- **Library Dependency: nwp500-python**: Upgraded from 7.4.6 to 7.4.8
  - **7.4.8 (2026-02-21)**: Reservation CRUD helpers
    - Added `fetch_reservations()`, `add_reservation()`, `delete_reservation()`, `update_reservation()` to `nwp500.reservations`
    - See [release notes](https://github.com/eman/nwp500-python/releases/tag/v7.4.8) for full details
- **Read-modify-write for reservations**: `set_reservation` now reads the current schedule and appends entries rather than replacing the full list
- **Service schema improvements**: Converted TOU service parameters from camelCase to snake_case for consistency with Home Assistant conventions
- **Auto-refresh behavior**: Reservation services (`set_reservation`, `update_reservations`, `clear_reservations`) now automatically request current state after successful writes
- **Service log messages**: Updated to "Registered NWP500 services" for clarity

### Fixed
- **State attribute conflict**: Renamed `state` extra attribute to `state_province` to avoid conflicts with Home Assistant's reserved `state` concept. The `state` key conflicted with MQTT integration where `state` refers to entity value, not geographic location.
- **Import cleanup**: Removed unused `DEVICE_TYPE_WATER_HEATER` import from mqtt_manager.py
- **TOU validation**: Added max 16 period validation to TOU schedule configuration
- **Code formatting**: Fixed ruff formatting issues

## [0.2.2] - 2026-02-15

### Changed
- **Library Dependency: nwp500-python**: Upgraded from 7.4.5 to 7.4.6
  - **7.4.6 (2026-02-13)**: Bug fixes and security improvements
    - Fixed `div_10()`/`mul_10()` converter consistency for all input types
    - Fixed reservation decoding out-of-bounds access
    - MQTT client now resubscribes to all topics after reconnection
    - Fixed subscription leak in `wait_for_device_feature()`
    - Prevents duplicate callback registration in subscription manager
    - `MqttCommandQueue` now raises on `QueueFull` instead of silently dropping
    - Removed hardcoded "GPM" unit from `recirc_dhw_flow_rate`; unit is now dynamic based on unit system
    - Fixed temperature rounding in `RawCelsius` Fahrenheit conversion
    - `is_metric_preferred()` now returns `False` instead of `None` when no unit system is set
    - Security: Redacted MQTT topics in subscription manager logging

### Fixed
- **Falsy value checks in MQTT commands**: Fixed `mqtt_manager.py` where `if temp:` / `if mode:` / `if days:` would incorrectly skip commands when value was `0`. Changed to explicit `is not None` checks.
- **Hardcoded flow rate unit**: Changed `recirculation_dhw_flow_rate` sensor from hardcoded "GPM" to dynamic unit from library, matching 7.4.6's unit-system-aware flow rate metadata.
- **Raw enum serialization**: Water heater extra state attributes now serialize enum values to strings instead of storing raw enum objects.
- **Timestamp precision**: Diagnostic sensor `connected_duration_seconds` now uses `time.time()` instead of `datetime.now().timestamp()` for correctness.

## [0.2.0] - 2026-02-08

### Changed
- **Unit System Synchronization**: Fixed temperature and flow rate unit conversions to properly respect Home Assistant's configured unit system
- **Code Quality**: Improved type safety and resource management

## [0.1.10] - 2026-01-26

### Changed
- **Library Dependency: nwp500-python**: Upgraded from 7.3.1 to 7.4.5
  - **7.4.5 (2026-01-26)**: Unit-aware fixes and temperature conversion improvements
    - Implemented unit-aware logic to resolve temperature conversion issues
    - Refactored water_heater and number platforms to use library-provided unit-aware values
    - Removed manual unit conversions and fallback logic
    - Removed device_class from differential temperature sensors to prevent incorrect offset application
    - Updated reservations to use unit-agnostic temperature parameter
    - Improved consistency in unit handling between Home Assistant and library

## [0.1.7] - 2026-01-25

### Fixed
- **MQTT Token Stale Error**: Fixed hanging integration caused by stale/expired tokens during MQTT client initialization
  - Ensured tokens are refreshed BEFORE creating the MQTT client, preventing "Tokens are stale/expired" errors
  - Resolves repeated MQTT connection failures and integration hang-ups after Home Assistant restart
  - Integration now properly refreshes authentication tokens before attempting MQTT connection

### Changed
- **Code Quality Improvements**: Enhanced type safety and resource management
  - Replaced generic `Any` type hints with proper `Device` types across all entity classes (`water_heater.py`, `switch.py`, `sensor.py`, `number.py`, `binary_sensor.py`, `entity.py`)
  - Improved code organization by centralizing mode mappings in `const.py` module, eliminating duplicate `MODE_TO_DHW_ID` definitions
  - Better type checking with explicit `Device` types instead of generic `Any` for improved IDE support and error detection

### Refactored
- **Service Handler Architecture**: Refactored service handlers from nested functions into testable `NWP500ServiceHandler` class
  - Improves testability and maintainability
  - Cleaner separation of concerns from integration setup logic
  - Easier to mock and test service handler behavior
- **Performance Optimization**: 
  - Added O(1) device lookup cache in `coordinator.py` to replace O(n) list iterations for faster device lookups
  - Replaced manual list management with `collections.deque(maxlen=20)` for timeout history tracking
  - Automatic circular buffer behavior without manual truncation logic
  - Reduces performance overhead of timeout history operations

## [0.1.9] - 2026-01-25

### Changed
- **Code Cleanup**: Removed temporary MQTT token refresh workaround from `mqtt_manager.py`
  - Workaround was replaced by proper library fix in nwp500-python 7.3.1
  - Library now handles token validation in `connect()` instead of requiring pre-validation
  - Simplifies integration code and eliminates redundant token refresh logic

## [0.1.8] - 2026-01-25

### Changed
- **Library Dependency: nwp500-python**: Upgraded from 7.2.3 to 7.3.1
  - **7.3.1 (2026-01-25)**: Removed strict token validity check from `NavienMqttClient.__init__()`, defers validation to `connect()` - resolves MQTT client creation with restored/expired tokens
  - **7.3.0 (2026-01-19)**: Dynamic Unit Conversion feature - all temperature, flow, and volume measurements now automatically convert based on user's region preference (Metric/Imperial)
    - Temperature fields convert between Celsius and Fahrenheit
    - Flow rate fields convert between LPM and GPM
    - Volume fields convert between Liters and Gallons
    - New `get_field_unit()` method to retrieve correct unit suffix for any field
    - New `unit_system` parameter on MQTT/API clients for explicit Metric/Imperial override
  - See [nwp500-python 7.3.0 Release Notes](https://github.com/eman/nwp500-python/releases/tag/v7.3.0) for complete unit conversion documentation

### Fixed
- **Authentication Error Handling**: Fixed false reauth prompts when network errors occur during authentication
  - Network errors during startup no longer trigger unnecessary reauthentication flows
  - Only genuine credential failures now prompt users to re-authenticate
  - Leverages `retriable` flag from nwp500-python v7.2.3 for intelligent error differentiation
- **Type Checking Configuration**: Fixed JSON syntax errors and editor configuration issues
  - Removed trailing commas from `pyrightconfig.json` for valid JSON
  - Added `stubPath` configuration pointing to valid directory
  - Created `.zed/settings.json` for proper Zed editor integration
  - Resolves editor complaints about missing typings directory

### Changed
- **Code Quality**: Simplified error handling logic by removing unnecessary hasattr checks
  - Direct access to `retriable` attribute makes code intention clearer
  - Improved maintainability with explicit dependency on nwp500-python v7.2.3+
- **Type Checking**: Migrated from Pyright to Basedpyright for faster, more robust type checking
  - Updated `.github/workflows/ci.yml` to use basedpyright instead of pyright
  - Faster type checking in CI pipeline without sacrificing accuracy
  - Better alignment with modern Python tooling ecosystem

## [0.1.5] - 2025-12-28

### Added
- **Full HACS Validation**: Enabled strict `hacsjson` and `integration_manifest` checks in CI pipeline
- **Enhanced Metadata**: Added `loggers` to `manifest.json` and improved `hacs.json` for better store visibility

### Changed
- **Updated to Python 3.13+**: Minimum Python version is now 3.13 (supports 3.13 and 3.14)
- **Updated to Home Assistant 2025.1.0+**: Aligned with latest Home Assistant requirements
- **Repository Visibility**: Switched to Public repository to support HACS validation and publication
- **Cleaned up Metadata**: Standardized `manifest.json` key order and removed unsupported fields to pass Hassfest validation

### Removed
- **Legacy Diagnostics**: Removed periodic background file writing to config directory
  - Background I/O removed to reduce disk wear and follow modern integration standards
  - Diagnostic data remains fully accessible via Home Assistant's native "Download Diagnostics" feature

## [0.1.4] - 2025-12-27

## [0.1.3] - 2025-12-27

### Added
- **Tank Volume Sensor**: New sensor displays tank capacity in gallons
  - Shows 50, 65, or 80 gallons based on device model
  - Enabled by default for easy visibility
  - No entity category (static device characteristic, not config/diagnostic)
  - Uses VolumeCode enum from nwp500-python v7.2.0
- **New Recirculation Sensors**: Added missing recirculation system sensors from nwp500-python v7.2.0
  - Recirculation Model Type Code - identifies installed recirculation hardware
  - Recirculation Software Version - recirculation controller firmware version
  - Recirculation Minimum Temperature - lower temperature limit for recirculation loop
  - Recirculation Maximum Temperature - upper temperature limit for recirculation loop
  - All recirculation sensors are disabled by default

### Changed
- **Updated to nwp500-python v7.2.0**: Adopted latest library version
  - No breaking changes affecting this integration (class renames were internal to library)
  - Enhanced VolumeCode enum provides better tank capacity identification
  - New temperature conversion classes improve type safety (internal to library)
  - See [nwp500-python v7.2.0 release notes](https://github.com/eman/nwp500-python/releases/tag/v7.2.0)

## Previous Releases

### Added (from v7.1.0)
- **New v7.1.0 Control Services**: Exposed new device control commands from nwp500-python v7.1.0
  - `nwp500.enable_demand_response` / `nwp500.disable_demand_response` - Utility demand response participation
  - `nwp500.reset_air_filter` - Reset air filter maintenance timer
  - `nwp500.set_vacation_days` - Configure vacation mode duration (1-365 days)
  - `nwp500.set_recirculation_mode` - Control recirculation pump mode (1-4)
  - `nwp500.trigger_recirculation` - Manual recirculation pump hot button trigger
  - All services support device selector for easy automation

### Changed
- **BREAKING: nwp500-python v7.1.0 API changes**: Updated MQTT control method calls to use `.control` property
  - All device control methods now accessed via `mqtt_client.control.method_name()`
  - Updated `request_device_status()`, `request_device_info()`, and all control commands
  - Periodic request methods consolidated to `start_periodic_requests()` with `PeriodicRequestType` enum
  - Required to support new capability checking system in library v7.1.0
- **Python 3.13+ match/case statements**: Refactored command dispatcher to use modern pattern matching
  - Replaced long if/elif chains with match/case for cleaner code
  - Leverages Python 3.13 structural pattern matching (PEP 634)
- **Python 3.13-3.14 optimizations**: Updated to leverage latest Python performance improvements
  - Dictionary operations benefit from 10-15% faster lookups and comprehensions
  - Improved function call performance reduces coordinator overhead
  - Native type hints (`X | Y`) instead of `Union[X, Y]`
  - `datetime.UTC` instead of `timezone.utc`
- **Updated Python target**: Ruff target-version set to `py313`
- **Dropped Python 3.12**: Removed py312 from test matrix, focusing on Python 3.13+
- **Removed Python 3.14**: Removed py314 from test matrix; pydantic-core lacks prebuilt wheels for Python 3.14

## [0.1.2] - 2025-12-18

### Added
- **MQTT Diagnostics**: New diagnostics support for troubleshooting connection issues
  - `MqttDiagnosticsCollector` integration for connection drop tracking
  - Home Assistant native diagnostics protocol support via `diagnostics.py`
  - Periodic diagnostic exports to Home Assistant config directory
  - Connection state tracking and event recording
- **Reservation Scheduling**: New services for managing programmed temperature/mode schedules
  - `nwp500.set_reservation`: Create a single reservation entry with user-friendly parameters
  - `nwp500.update_reservations`: Replace all reservations with a new set (advanced)
  - `nwp500.clear_reservations`: Remove all reservation schedules
  - `nwp500.request_reservations`: Request current reservation data from device
- Reservations allow automatic mode and temperature changes at scheduled times
- Supports up to 7 reservation entries per device
- Comprehensive test coverage for diagnostics module (10 new tests)

### Changed
- **BREAKING**: Minimum Home Assistant version now 2025.1.0 (Python 3.12+ required)
- **BREAKING**: Dropped support for Python 3.10 and 3.11
- Updated nwp500-python dependency to 6.1.1
- Updated awsiotsdk minimum version to 1.27.0
- Modernized codebase with Python 3.12 features (match/case statements)
- CI now runs on Python 3.12 and 3.13
- Test coverage increased to 82.71%

## Library Dependency: nwp500-python

This section tracks changes in the nwp500-python library that this integration depends on.

### v8.1.1 (2026-05-18)

#### Fixed
- **MQTT reconnection storm**: Two race conditions caused dozens of concurrent
  `_active_reconnect` calls within milliseconds, each tearing down the
  connection the previous one just established.
  - Stale `on_connection_interrupted` callbacks queued via
    `run_coroutine_threadsafe` could fire after `on_connection_resumed`
    cancelled `_reconnect_task` (setting it to `None`), bypassing the
    task-existence guard and spawning a new backoff loop against a healthy
    connection. Both `on_connection_interrupted` and `_start_reconnect_task`
    now check `is_connected()` before starting any reconnection.
  - Closing the old connection inside `_active_reconnect` / `_deep_reconnect`
    fired `_on_connection_interrupted_internal` from a background thread,
    queuing another reconnect that would tear down the brand-new connection.
    A boolean `_actively_reconnecting` flag (always cleared in `finally`)
    suppresses the reconnection-handler delegation during intentional teardown.

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v8.1.1

### v8.1.0 (2026-05-16)

#### Fixed
- **MQTT connection flapping after reconnect**: Old `MqttConnection` was never closed before creating a replacement, eventually causing two connections on the same client ID — AWS IoT would kick one off, triggering an infinite reconnect loop. Fixed by adding `MqttConnection.close()` and calling it in both `_active_reconnect()` and `_deep_reconnect()`.
- **Thread-safety race in `ensure_device_info_cached`**: `future.done()` check and `future.set_result()` were running on the AWS SDK thread without synchronisation. Both operations now execute atomically inside a `call_soon_threadsafe` callback.
- **ZeroDivisionError when `deep_reconnect_threshold` is 0**: Config validation now clamps the threshold to a minimum of 1.
- **Reconnect counter never incremented**: `total_reconnect_attempts` always reported 0; counter is now incremented on each `on_connection_interrupted` event.
- **`shortest_session_seconds` not JSON-serialisable**: `float('inf')` initial value replaced with `None` so diagnostics serialise correctly before any session completes.
- **`wait_for()` future not bound to running loop**: Used `asyncio.get_running_loop().create_future()` instead of bare `asyncio.Future()`.
- **Reservation temperature validation was US-only**: Validation now uses the active unit system — 35–65 °C in metric mode, 95–150 °F in US mode. Celsius users no longer receive spurious `ValueError` rejections.
- **Malformed reservation data silently dropped**: `build_reservation_entry` now logs a warning when reservation hex data contains unexpected trailing bytes.
- **Unknown `PeriodicRequestType` silently ignored**: Handler now logs an error and breaks instead of silently doing nothing.
- **Memory leak in device info cache**: `get_all_cached()` now evicts expired entries from the cache dictionary instead of only filtering them from the return value.

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v8.1.0

### v7.3.4 (2026-01-27)

#### Fixed
- **Delta temperature calculations**: Normalized deltas to use consistent units and device sensor
  offsets, preventing overstated temperature differences

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.3.4

### v7.2.3 (2026-01-16)

#### Fixed
- **Network Errors Triggering False Reauth**: Fixed issue where network errors during authentication startup were incorrectly triggering reauth prompts
  - Root cause: Network errors and invalid credentials were both raised as `AuthenticationError`, making them indistinguishable
  - Solution: Network errors in `sign_in()` and `refresh_token()` now set `retriable=True` flag
  - Impact: Integration can now distinguish transient network failures from actual credential failures
  - Home Assistant will retry automatically without prompting for reauthentication when network is unavailable

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.2.3

### v7.2.2 (2025-12-26)

#### Fixed
- **TOU Status Always Showing False**: Fixed `touStatus` field always reporting `False` regardless of actual device state
  - Root cause: Version 7.2.1 incorrectly changed `touStatus` to use device-specific 1/2 encoding, but the device uses standard 0/1 encoding
  - Solution: Use Python's built-in `bool()` for `touStatus` field (handles 0=False, 1=True naturally)
  - Device encoding: 0=OFF/disabled, 1=ON/enabled (standard Python truthiness)

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.2.2

### v7.2.0 (2025-12-23)

#### Breaking Changes
- **Class Renames**: `DeviceCapabilityChecker` → `MqttDeviceCapabilityChecker`, `DeviceInfoCache` → `MqttDeviceInfoCache`
  - These classes are MQTT-specific implementations
  - **No impact on this integration** - we don't use these classes directly

#### Added
- **VolumeCode Enum**: Tank capacity identification with human-readable text
  - `VOLUME_50GAL = 65`, `VOLUME_65GAL = 66`, `VOLUME_80GAL = 67`
  - `VOLUME_CODE_TEXT` dict provides display text (e.g., "50 gallons")
  - Used in `DeviceFeature.volume_code` field
- **Temperature Conversion Classes**: Type-safe temperature handling (`HalfCelsius`, `DeciCelsius`)
- **Protocol Converters Module**: Centralized device protocol conversion logic
- **Recirculation Fields**: Additional recirculation system sensors
  - `recirc_model_type_code`: Identifies installed recirculation hardware
  - `recirc_sw_version`: Recirculation controller firmware version
  - `recirc_temperature_min` / `recirc_temperature_max`: Temperature limits
- **Factory Function**: New `create_navien_clients()` for streamlined initialization

#### Changed
- **MQTT Module Reorganization**: Consolidated into cohesive `mqtt` package
- **CLI Framework**: Migrated from argparse to Click framework
- **Examples Reorganization**: Structured into beginner/intermediate/advanced categories

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.2.0

### v7.1.0 (2025-12-22)

#### Added
- **Device Capability System**: New device capability detection and validation framework
  - `DeviceCapabilityChecker`: Validates device feature support based on device models
  - `DeviceInfoCache`: Efficient caching of device information with configurable update intervals
  - `@requires_capability` decorator: Automatic capability validation for MQTT commands
  - `DeviceCapabilityError`: New exception for unsupported device features
- **Advanced Control Commands**: New MQTT commands for advanced device features
  - Demand response participation control
  - Air filter maintenance tracking reset
  - Vacation mode duration configuration
  - Water program reservation management
  - Recirculation pump control and scheduling
- **CLI Documentation Updates**: Comprehensive documentation updates for subcommand-based CLI
- **Model Field Factory Pattern**: New field factory to reduce boilerplate in model definitions

#### Changed
- **CLI Output**: Numeric values in status output now rounded to one decimal place for better readability
- `MqttDeviceController` now integrates device capability checking with auto-caching of device info
- **MQTT Control Refactoring**: Centralized device control via `.control` namespace
- **Logging Security**: Enhanced sensitive data redaction (MAC addresses consistently redacted)

#### Fixed
- Type annotation consistency: Optional parameters now properly annotated
- Multiple type annotation issues for CI compatibility
- Mixing valve field: Corrected alias field name
- Vacation days validation: Enforced maximum value validation
- CI linting: Fixed line length violations and import sorting issues
- Parser regressions: Fixed data parsing issues introduced in MQTT refactoring

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.1.0

### v7.0.1 (2025-12-18)

#### Fixed
- Minor bug fixes and improvements
- Fixed DREvent enum integration for DR Event Status sensor

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.0.1

### v7.0.0 (2025-12-18)

#### Key Changes
- **Python 3.13 minimum**: Library now requires Python 3.13+
- **Comprehensive enumerations module**: New type-safe enums for device control and status
  - `DhwOperationSetting`, `CurrentOperationMode`, `TemperatureType`, `CommandCode`, `ErrorCode`, etc.
  - Enums automatically serialize to human-readable names
- **Python 3.13 features**: PEP 695 type syntax, native `datetime.UTC`, native union syntax

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.0.0

### v6.1.1 (2025-12-08)

#### Added
- `MqttDiagnosticsCollector` class for detailed MQTT diagnostics
  - Track connection drops with error information
  - Record connection recovery events
  - Export diagnostic data as JSON for analysis
  - Helps diagnose and troubleshoot MQTT connection issues

#### Features
- Captures connection interruption events with error details
- Records connection success events with return codes and session state
- Provides JSON export functionality for offline analysis
- Designed for Home Assistant integration diagnostics

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.1.1

### v6.1.0 (2025-12-03)

**BREAKING CHANGES**: Temperature API simplified with Fahrenheit input

#### Changed
- `build_reservation_entry()` now accepts `temperature_f` (Fahrenheit) instead of raw `param` value
- `set_dhw_temperature()` now accepts Fahrenheit directly instead of raw integer
- Temperature conversion to half-degrees Celsius handled automatically by the library

#### Removed
- `set_dhw_temperature_display()` removed (was using incorrect conversion formula)

#### Added
- `fahrenheit_to_half_celsius()` utility function for advanced use cases

#### Fixed
- Temperature encoding bug in `set_dhw_temperature()` - was using incorrect "subtract 20" formula

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.1.0

### v6.0.8 (2025-12-02)

#### Changed
- Maintenance release, version bump for PyPI

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.8

### v6.0.7 (2025-11-30)

#### Features
- Added TOU (Time-of-Use) override support:
  - New binary sensor entity for TOU override status
  - New switch entity to control TOU override

#### Changed
- Updated nwp500-python dependency to 6.0.7

#### Fixed
- Minor bug fixes and improvements

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.7

### v6.0.6 (2025-11-24)

#### Bug Fixes
- Updated nwp500-python dependency to 6.0.6
- Minor bug fixes and improvements

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.6

### v6.0.5 (2025-11-21)

#### Bug Fixes
- Updated nwp500-python dependency to 6.0.5
- Minor bug fixes and improvements

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.5

### v6.0.4 (2025-11-21)

#### Bug Fixes
- Updated nwp500-python dependency to 6.0.4
- Minor bug fixes and improvements

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.4

### v6.0.3 (2025-11-20)

**BREAKING CHANGES**: Migration from custom dataclass-based models to Pydantic BaseModel implementations.

#### Removed
- Removed legacy dataclass implementations for models. All models now inherit from `NavienBaseModel` (Pydantic).
- Removed manual `from_dict` constructors.
- Removed field metadata conversion system.

#### Changed
- Models now use snake_case attribute names consistently; camelCase keys from API/MQTT are mapped automatically via Pydantic.

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.3

### v6.0.2 (2025-11-15)

#### Bug Fixes
- Fixed issue with MQTT connection stability

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.2

### v6.0.1 (2025-11-08)

#### Bug Fixes
- Fixed `DatetimeFormatError` when parsing device timestamps with fractional seconds
- Improved datetime parsing robustness

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.1

### v6.0.0 (2025-11-02)

**BREAKING CHANGES** - However, this integration was already compatible and required no code changes.

#### What Changed in the Library
- **Constructor Callbacks Removed**: `on_connection_interrupted` and `on_connection_resumed` constructor parameters removed from `NavienMqttClient`
  - Migration: Use event emitter pattern instead: `mqtt_client.on("connection_interrupted", handler)`
- **Exception Import Changes**: Backward compatibility re-exports removed from `api_client` and `auth` modules
  - Migration: Import exceptions from `nwp500.exceptions` or package root

#### Migration Status for this Integration
- Already using event emitter pattern (not constructor callbacks)
- All exception imports use correct module (`nwp500.exceptions`)
- No code changes required for v6.0.0 compatibility
- Full compatibility with new architecture

#### Benefits
- Multiple event listeners per event (not limited to one callback)
- Consistent API across all events
- Dynamic listener management (add/remove at runtime)
- Async handler support
- Cleaner architecture and imports

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.0

### v5.0.2 (2025)

#### Improvements
- **Bug Fix**: Fixed `InvalidStateError` when cancelling MQTT futures during disconnect
  - Prevents race condition when MQTT connection is being torn down
  - Improves stability during reconnection scenarios
  - Better handling of connection state transitions

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v5.0.2

### v5.0.0 (2025)

**BREAKING CHANGES** - This integration was updated to handle all changes.

#### What Changed in the Library
- **Exception Handling**: Library now uses specific exception types (`MqttNotConnectedError`, `MqttConnectionError`, `RangeValidationError`, etc.) instead of generic `RuntimeError` and `ValueError`
- **Python Version**: Minimum Python version is now 3.9 (was 3.8)
- **Type Hints**: Migrated to native type hints (PEP 585): `dict[str, Any]` instead of `Dict[str, Any]`

#### Migration Status for this Integration
- All exception handling updated to use new specific exception types
- Integration already uses Python 3.9+ minimum (via Home Assistant requirements)
- Type hints already use PEP 585 native syntax
- All imports and error handling patterns updated
- Full compatibility with new exception architecture

#### Improvements
- **Enterprise Exception Architecture**: Complete exception hierarchy for better error handling
  - Added `Nwp500Error` as base exception for all library errors
  - MQTT-specific exceptions: `MqttError`, `MqttConnectionError`, `MqttNotConnectedError`, `MqttPublishError`, `MqttSubscriptionError`, `MqttCredentialsError`
  - Validation exceptions: `ValidationError`, `ParameterValidationError`, `RangeValidationError`
  - Device exceptions: `DeviceError`, `DeviceNotFoundError`, `DeviceOfflineError`, `DeviceOperationError`
  - All exceptions include `error_code`, `details`, and `retriable` attributes

- **Exception Handling Improvements**:
  - All exception wrapping now uses exception chaining (`raise ... from e`) to preserve stack traces
  - Replaced 19+ instances of generic exceptions with specific types
  - Better error messages and user guidance
  - Structured logging support with `to_dict()` method on all exceptions

- **Critical Bug Fixes**:
  - Fixed thread-safe reconnection task creation from MQTT callbacks (prevents `RuntimeError: no running event loop`)
  - Fixed thread-safe event emission from MQTT callbacks
  - Fixed device control command codes (power-off/on now use correct command codes)
  - Fixed MQTT topic pattern matching with wildcards
  - Fixed missing `OperationMode.STANDBY` enum value
  - Robust enum conversion with fallbacks for unknown values

- **Code Quality**:
  - Modern Python type hints (PEP 585)
  - Better debugging capabilities
  - Cleaner, more maintainable codebase
  - Comprehensive test suite for exceptions

### v4.8.0 (2025)

#### Improvements
- **Token Persistence**: Added `stored_tokens` parameter to `NavienAuthClient.__init__()` for restoring previously saved tokens
- **Session Continuity**: Reduces API load and improves startup time by reusing valid authentication tokens across application restarts
- **Smart Authentication**: Automatically skips authentication when valid stored tokens are provided
- **Auto-Refresh**: Automatically refreshes expired JWT tokens or re-authenticates if AWS credentials expired
- **Rate Limit Prevention**: Avoids hitting API rate limits from frequent restarts

### v4.7.1 (2025)

#### Improvements
- **Bug Fixes**: Minor improvements and bug fixes from v4.7

### v4.7 (2025)

#### Improvements
- **Two-Tier MQTT Reconnection Strategy**: 
  - Quick reconnection (attempts 1-9) for fast recovery from transient network issues
  - Deep reconnection (every 10th attempt) with full credential refresh and subscription recovery
  - Unlimited retries - never gives up permanently
- **Enhanced Error Handling**: Replaced 25 catch-all exception handlers with specific exception types
- **New Public API**:
  - `NavienAuthClient.has_stored_credentials` property
  - `NavienAuthClient.re_authenticate()` method
  - `MqttSubscriptionManager.resubscribe_all()` method
- **Production-Ready MQTT Reconnection**: Never loses connection permanently, handles expired AWS credentials gracefully
- **Code Quality**: Improved error messages, better debugging capabilities, cleaner maintainable codebase

### v3.1.4 (2025)

#### Improvements
- **MQTT Reconnection**: Fixed MQTT reconnection failures due to expired AWS credentials
- **Connection Recovery**: Improved automatic recovery from connection interruptions

### v3.1.3 (2025)

#### Improvements
- **MQTT Reliability**: Improved MQTT reconnection reliability with active reconnection
- **Connection Stability**: Better handling of connection interruptions and recovery

### v3.1.2 (2025)

#### Improvements
- **Authentication**: Fixes 401 authentication errors with automatic token refresh
- **Reliability**: Improved session management and token handling

### v3.1.1 (2025)

#### Improvements
- **Documentation**: PEP 257 compliant docstrings for better IDE support
- **Code Quality**: 80 character line limit for improved readability
- **Comprehensive Documentation**: Enhanced API documentation

### v3.0.0 (2025)

**BREAKING CHANGES**

#### What Changed
- **Removed**: Deprecated `OperationMode` enum (fully replaced by `DhwOperationSetting` and `CurrentOperationMode`)
- **Removed**: Migration helper functions from v2.x
- **Clean API**: Streamlined enum structure for better type safety

#### Enhanced Type Safety
- **DhwOperationSetting**: User-configured mode preferences (Heat Pump, Electric, Energy Saver, High Demand, Vacation, Power Off)
- **CurrentOperationMode**: Real-time operational states (Standby, Heat Pump Mode, Hybrid Efficiency Mode, Hybrid Boost Mode)
- **Better IDE Support**: More specific enum types prevent accidental misuse

## [0.1.0] - 2025-10-23

### Added
- Initial release of Navien NWP500 Heat Pump Water Heater integration
- Support for water heater platform with operation mode control
- 40+ sensor entities for monitoring temperature, power, status, and diagnostics
- 15+ binary sensor entities for boolean status indicators
- Switch entities for power control
- Number entities for temperature setpoint control
- Real-time updates via MQTT connection
- Cloud authentication and device discovery
- Support for operation modes: Energy Saver, Heat Pump, High Demand, Electric
- Configuration flow for easy UI-based setup
- Device-based integration with proper device registry support
- Integration with nwp500-python library v3.1.2

[Unreleased]: https://github.com/eman/ha_nwp500/compare/v0.20.0...HEAD
[0.20.0]: https://github.com/eman/ha_nwp500/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/eman/ha_nwp500/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/eman/ha_nwp500/compare/v0.17.1...v0.18.0
[0.17.1]: https://github.com/eman/ha_nwp500/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/eman/ha_nwp500/compare/v0.16.2...v0.17.0
[0.16.2]: https://github.com/eman/ha_nwp500/compare/v0.16.1...v0.16.2
[0.16.1]: https://github.com/eman/ha_nwp500/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/eman/ha_nwp500/compare/v0.15.5...v0.16.0
[0.15.5]: https://github.com/eman/ha_nwp500/compare/v0.15.4...v0.15.5
[0.15.4]: https://github.com/eman/ha_nwp500/compare/v0.15.3...v0.15.4
[0.15.2]: https://github.com/eman/ha_nwp500/compare/v0.15.1...v0.15.2
[0.15.1]: https://github.com/eman/ha_nwp500/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/eman/ha_nwp500/compare/v0.14.5...v0.15.0
[0.14.4]: https://github.com/eman/ha_nwp500/compare/v0.14.3...v0.14.4
[0.14.3]: https://github.com/eman/ha_nwp500/compare/v0.14.2...v0.14.3
[0.14.2]: https://github.com/eman/ha_nwp500/compare/v0.14.0...v0.14.2
[0.14.0]: https://github.com/eman/ha_nwp500/compare/v0.3.0...v0.14.0
[0.1.5]: https://github.com/eman/ha_nwp500/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/eman/ha_nwp500/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/eman/ha_nwp500/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/eman/ha_nwp500/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/eman/ha_nwp500/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/eman/ha_nwp500/releases/tag/v0.1.0

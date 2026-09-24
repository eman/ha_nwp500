# External control (protocol 0)

An optional feature that lets an external scheduler control the NWP500
through Home Assistant. The scheduler never calls this integration's
services and does not need to know it exists. It publishes a **control
intent**: what the heater should do, and when. The feature reads the intent
from a Home Assistant entity, carries it out with the integration's own
client, verifies every write, restores the heater when the intent ends or
goes stale, and reports through entities.

**Status: experimental.** Protocol `0` ships in pre-releases. Protocol `1`,
with compatibility promises, follows only after a staged live cut-over on a
real heater. The feature is off by default, and enabling it starts in
`shadow` mode, which writes nothing to the heater.

The complete specification is
[issue #158](https://github.com/eman/ha_nwp500/issues/158). Section numbers
below refer to it. This page is the consumer's view: how to enable the
feature, what to publish, and what to read back. The machine-readable
schema is [`external-control-protocol-0.schema.json`](external-control-protocol-0.schema.json),
and [`examples/`](examples/) holds sample documents.

## Implementation status

The feature is delivered in steps (§9). This table is kept current on the
feature branch.

| Step | Contents | State |
|---|---|---|
| 1 | This page, the JSON Schema and the examples | Done |
| 2 | Options toggle and the disabled-path regression test; intake, validation and the stored intent; the capability entity; `shadow` as the default mode; heartbeat; unload without writes | Done |
| 3 | Shadow execution: translation into entries and direct writes, the entry budget, closing and daily-revert entries, overrides, the "wanted" state entities, the Disable button's restore, simulated restore | Done |
| 4 | Live writes, per directive type, after owner-present tests on a real unit | Not started |
| 5 | Protocol `1` after a staged live cut-over | Not started |

Until step 4 lands, `live` cannot be selected, `live_types` is always empty,
and nothing is written to the heater. The baseline is **provisional**: taken
from the device's settings when the feature starts, and marked
`provisional: true` in the capability entity, until a baseline is declared
on the first switch to `live`.

## Enabling the feature

Everything is configured in the integration's options (Settings >
Devices & services > Navien NWP500 > Configure). The first page holds the
existing update interval and the **External control (experimental)** toggle.
Turning the toggle on opens a second page:

| Option | Default | Notes |
|---|---|---|
| Intent entity | none | Required. Any entity; see below |
| Mode | `shadow` | `shadow` evaluates and reports but writes nothing. `disabled` reverts to the baseline once and then writes nothing. `live` arrives with step 4 |
| Hold-off supported | off | Enable only after testing `hold_off` on your unit (§5.6) |
| Hold-off margin (°F) | 2 | How far below the upper-tank temperature a hold-off sets the setpoint |
| Surplus entity | none | A `binary_sensor` (on = surplus) or a kW `sensor`. Required for `surplus_grant` |
| Surplus threshold (kW) | 0.45 | For a numeric surplus sensor: surplus when the value is at or above this |
| Setpoint minimum / maximum | device range | Optional tighter bounds. Empty follows the device's own `dhw_temperature_min` / `max` |
| Allowed modes | `energy_saver` | Modes a `mode` directive may name |
| Assisted mode | `energy_saver` | The mode a scheduler should use for faster recovery. Must be one of the allowed modes |
| May switch TOU off to land a mode | off | §5.9 |
| Minimum run before stop (min) | 120 | A running compressor is never stopped before this (§5.5) |
| Reservation entry limit / reserve | 7 / 2 | The most entries the feature asks the device to hold, and how many it keeps back for closing and daily-revert entries (§5.3) |
| Daily revert time | 03:00 | Local time at which the heater reverts itself to the baseline (§5.4) |

Changing any option updates the capability entity, and so its version.

While the feature is off, nothing of it loads: no imports, listeners,
entities, stored data, timers or writes. Turning it off again removes its
entities and deletes its stored data.

## The intent entity

The scheduler publishes each intent to **any Home Assistant entity**:

- Its **state** must change on every new intent. Use the `intent_id`.
- Its **attributes** are the intent document, top-level keys as attributes.

The feature listens for the entity's state changes and stores the last
accepted intent, so a restart with the source unavailable does not lose it.

The typical source is an MQTT sensor from discovery, pointed at a retained
topic that carries the document. Home Assistant then keeps the latest intent
across restarts too. A discovery payload for it:

```json
{
  "name": "Water heater intent",
  "unique_id": "water_heater_intent",
  "state_topic": "scheduler/water_heater/intent",
  "value_template": "{{ value_json.intent_id }}",
  "json_attributes_topic": "scheduler/water_heater/intent"
}
```

A REST sensor or a template sensor works the same way. Exclude the intent
entity from the recorder: its attributes are a document, not history.

For a quick test without a scheduler, a state posted to the REST API
(`POST /api/states/sensor.water_heater_intent` with the document as
`attributes`) is picked up the same way. Such a state does not survive a
restart; the feature then falls back to its stored copy of the intent.

## The intent document

### Top level

| Key | Type | Required | Meaning |
|---|---|---|---|
| `protocol` | string | yes | `"0"`. Any other major version is rejected (`unsupported_protocol`) |
| `intent_id` | string, at most 64 characters | yes | Unique per intent |
| `issued_at` | ISO 8601 with offset | yes | When the scheduler made it |
| `valid_until` | ISO 8601 with offset | yes | After this the intent is **stale** and the heater is restored. Must be later than `issued_at` |
| `directives` | list | yes | Ordered by `start`. **An empty list is an explicit "no intent"**: run on the baseline |
| any other key | any | no | Opaque. Echoed on the acknowledgement entity, for example `plan_id` |

### Directives

Every directive has `id` (unique within the intent), `type`, `start` and
`end` (ISO 8601 with offset, `end` later than `start`). Other keys are
opaque and echoed back.

| `type` | Extra keys | Meaning |
|---|---|---|
| `charge` | `target_f` **or** `target_c` | From `start`, heat until the tank reaches the target or `end` arrives, whichever is first. `end` is the latest the charge may run. Completion is the compressor stopping, never "reached setpoint": under TOU a recovery can stop short of the setpoint by design |
| `hold_off` | none | No heating starts in the window, within what a lowered setpoint can prevent (§5.6). The lower-tank trigger does not move with the setpoint, so a large draw can still start the heater |
| `mode` | `mode`, `request` (bool, default false) | Run in that operation mode for the window. `request: true` marks a person's request: a cycle it starts, or one already running when it arrives, runs to completion |
| `surplus_grant` | `max_f` **or** `max_c` | While the compressor is already running and the surplus signal is on, the setpoint may be raised up to the maximum. Never used to start a cycle |

Temperatures are given in exactly one unit. The feature converts and applies
the device's half-degree-Celsius resolution.

Mode names: `heat_pump`, `energy_saver`, `high_demand`, `electric`.
`vacation` and `power_off` are never accepted in a directive.

### Validation

The document is **rejected whole**, with the reason on the acknowledgement
entity, if any of these hold:

- JSON types or required keys are wrong (`invalid_document`);
- `protocol` is unsupported (`unsupported_protocol`);
- `valid_until` is not later than `issued_at` (`invalid_validity`);
- `valid_until` is already past on receipt (`stale_on_receipt`);
- directive ids are duplicated (`duplicate_directive_id`);
- a directive has `end` not later than `start` (`invalid_window`);
- a `charge` overlaps a `hold_off` (`charge_overlaps_hold_off`);
- a `surplus_grant` overlaps a `mode` (`grant_overlaps_mode`).

`mode` may overlap `charge` and `hold_off`; `surplus_grant` may overlap
`charge` and `hold_off`. A rejected document leaves the previously accepted
intent in force until that intent's own `valid_until`.

Otherwise the document is accepted and each directive is checked against the
capability declaration. A directive that does not fit is **rejected on its
own** while the rest proceed:

| Reason | When |
|---|---|
| `type_unsupported` | Its type is not in `supported_directives` |
| `type_not_live` | Its type's live switch is off. It is evaluated as in shadow |
| `out_of_bounds` | A temperature outside `setpoint_min`–`setpoint_max` |
| `mode_not_allowed` | A `mode` not in `allowed_modes` |
| `too_short` | A `charge` shorter than `min_run_before_stop_min` |
| `entry_budget` | Its reservation entries do not fit (§5.3) |
| `in_past` | `end` is already past |

### Example

```json
{
  "protocol": "0",
  "intent_id": "i-20261004T0500-7",
  "issued_at": "2026-10-04T05:00:12-07:00",
  "valid_until": "2026-10-04T06:15:00-07:00",
  "plan_id": "opaque-to-the-feature",
  "directives": [
    {"id": "d1", "type": "hold_off", "start": "2026-10-04T05:00:00-07:00", "end": "2026-10-04T10:30:00-07:00"},
    {"id": "d2", "type": "charge", "start": "2026-10-04T10:30:00-07:00", "end": "2026-10-04T14:30:00-07:00", "target_f": 140, "purpose": "demand"},
    {"id": "d3", "type": "surplus_grant", "start": "2026-10-04T11:00:00-07:00", "end": "2026-10-04T14:00:00-07:00", "max_f": 146}
  ]
}
```

More in [`examples/`](examples/): an explicit "no intent", and a person's
request served with an assisted mode and a top-up charge.

## Entities

All belong to the device and are prefixed with its name. Unique ids are
`<mac>_control_<key>`. Key facts are entity **states**, so history and
`mqtt_statestream` with `publish_attributes: false` carry them.

### Capabilities: `sensor.<device>_control_capabilities`

The state is the declaration's **version**, a short hash of the attributes.
It changes whenever the declaration does, so a consumer watching states
knows to re-read. The attributes:

| Attribute | Meaning |
|---|---|
| `protocols` | Supported protocol versions, `["0"]` |
| `feature_version` | The integration's version |
| `mode` | `shadow`, `live` or `disabled` |
| `live_types` | Directive types whose live switch is on |
| `supported_directives` | Types the feature will execute. `hold_off` needs the option; `surplus_grant` needs a surplus entity |
| `setpoint_min_f`, `setpoint_max_f`, `setpoint_min_c`, `setpoint_max_c` | The bounds the feature writes within. Options, defaulting to the device's own range. Absent until the device's feature data has arrived |
| `hold_off_margin_f` | How far below the upper-tank temperature a hold-off sets the setpoint |
| `setpoint_resolution_c` | 0.5 on the NWP500, so a model can quantise exactly as the heater does |
| `allowed_modes` | Modes a `mode` directive may use |
| `assisted_mode` | The mode a scheduler should use for faster recovery. Read this instead of naming a mode, so a plan works with other heaters |
| `telemetry` | Entity ids to read for this heater: `delivery_temperature` (upper tank), `compressor_running`, `power`; and `delivery_temperature_dip_f` with `delivery_temperature_dip_min`, the transient dip the delivery temperature shows during a draw without the tank being depleted (3.4 °F for about 3 minutes on the NWP500's upper probe) |
| `tou_off_for_mode` | Whether the feature may switch TOU off to land a mode |
| `min_run_before_stop_min` | A running compressor is never stopped before this |
| `reservation_entry_limit`, `reservation_entry_reserve` | The entry budget (§5.3) |
| `daily_revert_time` | Local time of the device-side daily revert |
| `setpoint_change_mid_cycle` | Whether a setpoint write is used to stop or extend a running cycle (true on the NWP500) |
| `baseline` | The configuration restored to: `version`, `mode`, `setpoint_f`, `tou_enabled`, `reservations_enabled`, `reservations`. `null` until declared, which happens the first time `live` is chosen |

### State entities

| Entity | State | Attributes |
|---|---|---|
| `sensor.<device>_control_intent` | The `intent_id` being worked on, or `none` | `issued_at`, `valid_until`, `received_at`, `directive_count`, the opaque top-level keys |
| `sensor.<device>_control_ack` | `applied`, `partly_applied`, `rejected`, `shadow` or `none`, for the most recent document | `intent_id`, `reason` (document-level rejection), `directives`: one entry per directive with `id`, `status` (`applied`, `pending`, `partly_applied`, `rejected`, `shadow`), `reason`, and its opaque keys |
| `sensor.<device>_control_heartbeat` | Timestamp, updated at least every 15 minutes, including in shadow. This is how a consumer knows the feature is alive | none |
| `sensor.<device>_control_wanted_mode` | The mode the feature wants now. In shadow, what it would write | `suspended_by` (vacation, power_off, anti_legionella or null), `holds` (why the wanted setpoint is being held: `compressor_min_run`, `request_cycle`, `no_reversal_in_cycle`, `daily_revert`), `restore_pending`, `baseline_version`, `baseline_provisional` |
| `sensor.<device>_control_wanted_setpoint` | The setpoint it wants now, in Home Assistant's unit | `setpoint_raw` (half-degrees Celsius), `surplus_raised`, `holds` |
| `binary_sensor.<device>_control_wanted_tou` | Whether it wants TOU on | none |
| `sensor.<device>_control_wanted_reservation_hash` | The `schedule_hash` its wanted reservation list would produce, comparable with the Reservation Schedule sensor | `entry_count`, `enabled`, `entries`, `owned` (the entries the feature owns: kind `start`, `closing` or `daily_revert`, the directive, when it fires) |
| `sensor.<device>_control_last_restore` | The last restore's reason: `expiry`, `intent_ended`, `stale_intent`, `startup`, `override_expired`, `daily_revert` or `disabled` | `at`, `matches_baseline`, `pending` (a restore waiting for the compressor) |
| `binary_sensor.<device>_control_restore_matched` | Whether the last restore read back as the baseline. In shadow, whether the device is at the baseline | none |
| `binary_sensor.<device>_control_override` | On while a person's change is being honoured | `field`, `value`, `detected_at`, `expires_at`, `fields` |

### Controls

`button.<device>_control_disable` switches the feature to `disabled`.
Nothing on the dashboard switches it to `live`: that, enabling, and
changing bounds happen in the options flow.

### Auditing shadow mode

In shadow the "wanted" entities are what the feature would write. Compare
them with the device's own entities: the water heater's mode and setpoint,
the TOU switch, and the Reservation Schedule sensor's `schedule_hash`. A
difference is the feature's intended change; the acknowledgement entity says
which directive caused it, and the wanted-mode entity's `holds` says when a
change is being deferred and why.

Overrides work the same way in shadow: any change to the setpoint, mode, TOU
or the reservation list that the feature did not make itself is a person's,
and the wanted state follows it until it is reverted or the daily revert
time passes.

## Behaviour in brief

The full rules are in the specification. The points a scheduler must know:

- **Every write is a possible start.** A setpoint or mode write makes the
  device re-evaluate. Outside a TOU window, a setpoint left above the upper
  tank started the compressor within about 30 seconds in 112 of 117 writes.
  The feature reports each write; a controller's model should project the
  starts its directives cause.
- **A running compressor is not stopped** before `min_run_before_stop_min`,
  except by the daily revert and by disabling the feature, and a cycle under
  a `mode` directive with `request: true` always runs to completion.
- **Every non-baseline state has an end**: the directive's `end`, the
  intent's `valid_until`, or the daily revert time, whichever comes first.
  The daily revert is also written as a device-side reservation entry, so
  the heater reverts itself even if Home Assistant is down.
- **Unload and restart write nothing.** A restore is itself a write that
  often starts the compressor.
- **A mode written inside a TOU window may be held** and take effect when
  the window ends, possibly hours later. A mode counts as applied only when
  the heater's behaviour confirms it.
- **While Vacation or power-off is active, or an Anti-Legionella cycle is
  running**, the feature writes nothing and withdraws its pending entries.
  It resumes on exit.
- **A person's change** to the setpoint, mode, TOU or the reservation list
  is honoured until they revert it or until the daily revert time.

Device behaviour these rules rest on is documented in `nwp500-python`:
*What starts a recovery*, *The TOU recovery cap*, and *Reservations and mode
writes during a TOU window* in the scheduling how-to.

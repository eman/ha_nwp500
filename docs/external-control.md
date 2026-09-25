# External control (protocol 1)

An optional feature that lets an external scheduler control the NWP500
through Home Assistant. The scheduler publishes a **plan**: a timeline of the
states the heater should be in. The feature programs that plan into the
heater's own weekly reservation list, so the heater carries it out itself and
keeps following it if Home Assistant, the feature or the scheduler becomes
unavailable.

The feature is off by default. Enabling it starts in `shadow` mode, which
plans and reports the reservation list it would write and writes nothing to
the heater. `live` writes the list; choosing it first shows the heater's own
program for confirmation, which disabling restores. Live mode was cut over in
stages on a real heater before protocol `1` was declared.

The complete specification is [`external-control-spec.md`](external-control-spec.md),
also published as [issue #158](https://github.com/eman/ha_nwp500/issues/158).
Section numbers below refer to it. This page is the consumer's view: how to
enable the feature, what to publish, and what to read back. The
machine-readable schema is
[`external-control-protocol-1.schema.json`](external-control-protocol-1.schema.json),
and [`examples/`](examples/) holds sample plans. Protocol `1` comes with
compatibility promises (specification section 1.3): within major version 1,
documents stay valid and changes to entities are additive. Documents that
say `"0"` are still accepted and mean the same.

## Implementation status

| Step | Contents | State |
|---|---|---|
| 1 | The specification, the JSON Schema and the examples | Done |
| 2 | Options toggle and the disabled-path regression test; intake, validation and the stored plan; the capability entity; `shadow` as the default mode; heartbeat; unload without writes | Done |
| 3 | Shadow programming: the owner's program; segments into entries, the horizon, the budget and near-term entries; reading the list; surplus grants; the program, in-sync, programmed-until and wanted entities; people's changes | Done |
| 4 | The device tests in section 8 of the specification | Done on 2026-09-24 and 25, except test 8 (no cycle could be started) and the off-cloud half of test 11 |
| 5 | Live list writes: segments with a single allowed mode, then more modes, then grants | Done; cut over in stages on a real heater on 2026-09-25 (specification section 8.1) |
| 6 | Protocol `1` after the staged live cut-over | Done: compatibility promises in section 1.3, a JSON Schema, and the examples |

In shadow every write the planner asks for is committed as **simulated**:
the last write entity shows it with `simulated: true`, and the program
entities show the list as if it had been written. See
[Live mode](#live-mode) for what changes in `live`. The constant
`CONTROL_LIVE_AVAILABLE` in `const.py` is a kill switch: set to `False`, the
options form stops offering `live` and a `live` option runs as shadow.

## Enabling the feature

Everything is configured in the integration's options (Settings >
Devices & services > Navien NWP500 > Configure). The first page holds the
update interval and the **External control** toggle. Turning
the toggle on opens a second page:

| Option | Default | Notes |
|---|---|---|
| Intent entity | none | Required. Any entity; see below |
| Mode | `shadow` | `shadow` plans and reports and writes nothing. `live` writes the list (only offered once live mode is switched on in code). `disabled` hands the heater back to the owner's program once and then writes nothing |
| Live: segments, surplus grants | off, off | Only with `live`. Segments off: live behaves as shadow. Grants need segments |
| Surplus entity | none | A `binary_sensor` (on = surplus) or a kW `sensor`. Required for surplus grants |
| Surplus threshold (kW) | 0.45 | For a numeric surplus sensor: surplus when the value is at or above this |
| Setpoint minimum / maximum | device range | Optional tighter bounds. Empty follows the device's own `dhw_temperature_min` / `max`. A plan's `"min"` setpoint means the minimum |
| Allowed modes | `energy_saver` | Modes a segment may use. A cut-over should start with one |
| Assisted mode | `energy_saver` | The mode a scheduler should use for faster recovery. Must be one of the allowed modes |
| Minimum run before lowering a surplus raise (min) | 120 | Section 5.7 |
| Reservation entry limit / reserve | 16 / 2 | The most entries the feature uses, and how many are kept free for changes needed now. The unit tested held 32; larger lists are untested |

Changing any option updates the capability entity, and so its version.
Options from the first draft of the specification are removed the next time
the form is saved.

While the feature is off, nothing of it loads: no imports, listeners,
entities, stored data, timers or writes. Turning it off again removes its
entities and deletes its stored data.

## The intent entity

The scheduler publishes each plan to **any Home Assistant entity**:

- Its **state** must change on every new plan. Use the `intent_id`.
- Its **attributes** are the plan, top-level keys as attributes.

The feature listens for the entity's state changes and stores the last
accepted plan. At start-up it uses the newer of the stored plan and the
entity's document, by `issued_at`.

The typical source is an MQTT sensor from discovery, pointed at a retained
topic that carries the plan:

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
(`POST /api/states/sensor.water_heater_intent` with the plan as
`attributes`) is picked up the same way. Such a state does not survive a
restart; the feature then keeps its stored copy of the plan.

## The plan

### Top level

| Key | Type | Required | Meaning |
|---|---|---|---|
| `protocol` | string | yes | `"1"`, or `"1.x"`; `"0"` is still accepted |
| `intent_id` | string, at most 64 characters | yes | Unique per plan |
| `issued_at` | ISO 8601 with offset | yes | An older plan never replaces a newer one (`superseded`) |
| `segments` | list | yes | The timeline. **An empty list stops the plan**: every programmed entry is withdrawn and the heater keeps its state |
| `grants` | list | no | Surplus grants |
| any other key | any | no | Opaque. Echoed on the plan entity, for example `plan_id` |

There is no validity period. The last segment holds until a new plan
arrives, so make it a state you are willing to hold indefinitely.

### Segments

A segment gives the heater's state from its `start` until the next segment's
`start`. Segments must be in increasing order of `start`, which is truncated
to the minute.

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique across segments and grants |
| `start` | yes | ISO 8601 with offset |
| `setpoint_f`, `setpoint_c` or `setpoint: "min"` | exactly one | The setpoint. Numbers are quantised to half a degree Celsius. `"min"` is the setpoint minimum option, else the device's minimum |
| `mode` | on the first segment | `heat_pump`, `energy_saver`, `high_demand` or `electric`. A later segment without one keeps the previous mode |

`vacation` and `power_off` are never accepted. Entries are skipped during
Vacation, so the plan's next entry would never end it. Whether an entry with
the power-off mode powers the heater off is untested, and the mode command
with that value switched the unit tested to Energy Saver (#160). Use
`setpoint: "min"` for effectively off.

### Surplus grants

| Key | Required | Meaning |
|---|---|---|
| `id`, `start`, `end` | yes | The window |
| `max_f` or `max_c` | yes | The highest setpoint a raise may use |

Within a grant's window, while the compressor is already running and the
segment in force is in `heat_pump` mode, the feature raises the setpoint to
the grant's maximum once surplus has been on for 10 minutes. It adds a guard
entry at the grant's end, so the device lowers the setpoint on its own if Home
Assistant stops. It lowers the raise when the compressor stops, or once the
minimum run has passed and surplus has been off for 15 minutes. It raises at
most once per compressor cycle, and never to start one.

### Validation

A plan is **rejected whole**, and the plan in force stays, for:
`invalid_document`, `unsupported_protocol`, `duplicate_id`,
`unordered_segments`, `out_of_bounds` (a segment's setpoint),
`mode_not_allowed` or `superseded`. One bad segment rejects the plan, because
skipping it would leave the segment before it in force over its time.

A **grant** is rejected on its own for `grants_unsupported` (no surplus
entity), `invalid_window`, `overlapping_grant`, `out_of_bounds` or `in_past`.

### Example

```json
{
  "protocol": "1",
  "intent_id": "i-20261004T0500-7",
  "issued_at": "2026-10-04T05:00:12-07:00",
  "segments": [
    {"id": "s1", "start": "2026-10-04T05:00:00-07:00", "setpoint": "min", "mode": "heat_pump", "purpose": "hold_off"},
    {"id": "s2", "start": "2026-10-04T10:30:00-07:00", "setpoint_f": 140, "purpose": "charge"},
    {"id": "s3", "start": "2026-10-04T14:30:00-07:00", "setpoint_f": 135, "mode": "energy_saver"},
    {"id": "s4", "start": "2026-10-04T22:00:00-07:00", "setpoint": "min"}
  ],
  "grants": [
    {"id": "g1", "start": "2026-10-04T11:00:00-07:00", "end": "2026-10-04T14:00:00-07:00", "max_f": 146}
  ]
}
```

`s1` has begun when the plan arrives, so it starts through an entry two
minutes ahead, at 05:03. The other segments become entries at 10:30, 14:30
and 22:00. If Home Assistant stops, the heater still runs them. More in
[`examples/`](examples/).

## How a plan becomes entries

- **One entry per segment**, carrying its mode and setpoint: a device entry
  always sets both. A segment with the same state as the one before gets no
  entry (`merged`).
- **Near-term entries.** A change needed now is an entry for the first minute
  at least two minutes ahead: a segment already begun, a surplus raise or
  lower, and re-asserting the segment after Vacation or power-off.
- **Horizon.** Entries are programmed at most 144 hours ahead, because a
  weekly entry cannot say which week. Later segments are `scheduled` and
  programmed as time passes.
- **Budget.** Entries fit within the entry limit, minus every other entry on
  the device, minus the reserve. Segments that do not fit are `scheduled` and
  programmed as earlier entries fire.
- **Fired entries** are removed in the next write, and within a day at most.
  While the feature is unavailable they stay, so after a week the device
  repeats the programmed run.
- **Your own entries** stay on the device. While live, the feature switches
  each one off by its own enable flag, so none fires against the plan, and
  switches them back on when it is disabled. They count against the entry
  limit.
- **One enabled entry per minute.** A plan entry that would share a weekday
  and minute with another enabled entry moves a minute later, with the
  warning `moved_1_min`. It may share one with a switched-off entry, such as
  your own while live: the device fires only the enabled one.
- **Replacing a plan.** Entries the new plan also wants are kept. A plan
  republished unchanged writes nothing, so it does not undo a person's
  change.

## Entities

All belong to the device. Unique ids are `<mac>_control_<key>`.

| Entity | State | Attributes |
|---|---|---|
| Control Capabilities | The declaration's version | The declaration (below) |
| Control Plan | The `intent_id` in force, or `none` | `issued_at`, `received_at`, `segment_count`, `grant_count`, the opaque keys |
| Control Acknowledgement | `shadow`, `programmed`, `partly_programmed`, `pending`, `rejected` or `none` | `intent_id`, `reason`, `detail`, `segments` and `grants`: each with `id`, `status`, `reason`, `warnings`, `fires_at`, `in_force`, and its opaque keys |
| Control Program Hash | The `schedule_hash` of the list the feature wants on the device | `entry_count`, `entries`: each marked `owner`, `foreign`, `plan`, `near_term` or `guard` |
| Control In Sync | On when the device's list hashes the same as the program | `device_hash`, `read_at` |
| Control Programmed Until | How far the device's copy of the plan reaches | `complete`, `scheduled` |
| Control Next Entry | When the next feature entry fires | `mode`, `setpoint_f`, `setpoint_c`, `kind`, `serves` |
| Control Wanted Mode, Control Wanted Setpoint | The state the plan puts the heater in now, with any surplus raise | `segment`, `grant` |
| Control Surplus Raise | On while a raise is in force | `grant`, `raised_at`, `fires_at`, `setpoint_f`, `setpoint_c` |
| Control Last Write | When the list was last written | `reason`, `added`, `removed`, `confirmed`, `simulated`, `owner_state` |
| Control Override | On while a person's change is reported | `field`, `value`, `detected_at`, `segment`, `reports` |
| Control Heartbeat | Updated at least every 15 minutes | none |
| Disable External Control (button) | | Switches the feature to `disabled` |

Segment statuses are `shadow`, `scheduled`, `merged` and `ended` in shadow;
live adds `pending`, `programmed`, `in_force`, `failed` and `removed`.
Grant statuses are `waiting`, `raised`, `ended` and `rejected`; live adds
`failed`, and `shadow` with reason `not_live` while grants are not live. The
last write's `reason` is one of `plan`, `cleanup`, `near_term`,
`grant_raise`, `grant_lower`, `precedence_exit`, `power_off`, `takeover` and
`disable`.

### The capability declaration

| Attribute | Meaning |
|---|---|
| `protocols`, `feature_version`, `mode`, `live` | What runs, and the live switches |
| `setpoint_min_f` / `_c`, `setpoint_max_f` / `_c`, `setpoint_resolution_c` | The bounds, and the device's half-degree resolution. Absent until the device's feature data has arrived |
| `allowed_modes`, `assisted_mode` | The modes a segment may use, and the one for faster recovery |
| `horizon_h`, `near_term_lead_min`, `entry_limit`, `entry_reserve`, `entries_available` | How entries are budgeted. `entries_available` changes as entries fire, so it is left out of the version |
| `grants_supported`, `grant_rules` | Whether grants can run, and their timing |
| `owner_program` | What disabling restores: `declared`, `mode`, `setpoint_f`, `setpoint_c`, `reservations_enabled`, `entries`. `declared: false` is the provisional snapshot shadow uses |
| `lower_trigger_f` | 104.9: the lower-tank turn-on temperature, which does not follow the setpoint |
| `setpoint_write_starts_recovery`, `setpoint_write_stops_compressor`, `entry_mode_in_tou_window`, `list_write_starts_recovery`, `unchanged_entry_starts_recovery`, `entries_fire_when_powered_off`, `entries_fire_in_vacation` | Device facts a scheduler's model needs; the last five were measured on the unit tested |
| `telemetry` | Entity ids for the delivery temperature, the compressor and power, and the delivery-temperature dip to ignore |

## Live mode

Live mode writes the plan into the heater's reservation list.

- **Going live.** Choosing `live` shows the heater's current program for
  confirmation: its mode, setpoint, reservation switch and entries. That is
  the owner's program, which disabling restores. Live does not run without
  it.
- **Taking the list over.** The first write, once a plan is in force, turns
  the reservation switch on and switches each of the owner's entries off by
  its own flag. The entries stay on the heater.
- **Every write** reads the list first, keeps entries the feature does not
  own, writes the list whole, and counts only once the heater reads back the
  new list. An unconfirmed write is retried once after a minute; after that
  its segments or grants are `failed` with reason `write_not_confirmed`, and
  writing pauses for 15 minutes. Changes that arrive during a write go into
  the next one. A write whose confirmation was lost may still have landed:
  the next read settles which entries are the feature's, so they are never
  mistaken for someone else's.
- **Read-back.** After each entry's minute, plus the poll interval and a
  minute, the heater's setpoint and mode are compared with the entry's. A
  difference makes the segment `failed` with `not_applied_on_device`, unless
  it is a mode held by a TOU window: then it stays `in_force` with
  `held_in_tou_window`. A person's change in the meantime explains any
  difference. A mode is confirmed by what the heater does: Heat Pump by
  running without an element, a mode that uses an element by an element
  running. Each segment in force shows `mode_confirmed`. Behaviour that
  contradicts the mode fails the segment. A mode held by a TOU window is
  checked again when the heater applies it or the window ends. Disabling
  reads its direct write back from the heater's status.
- **Statuses** become `pending`, `programmed`, `in_force`, `failed` and
  `removed`, and the acknowledgement's state `programmed`,
  `partly_programmed` or `pending`.
- **People's changes.** A plan entry a person deletes is not written again
  (`removed`). A person turning the reservation switch off keeps it off.
- **Leaving live** for shadow, or switching segments off, first hands the
  heater back as disabling does, so shadow starts from the owner's program.
- **Disabling** removes the feature's entries, gives the owner's entries
  their flags and the reservation switch back, then writes the owner's
  state: the one set by the owner's latest enabled entry, else the declared
  mode and setpoint. That direct write is skipped in Vacation or power-off.
  A failed hand-back is retried once after a minute, then at the next start.
  Handing back works even with live mode switched off in code, so a heater
  left holding the feature's entries can always be returned.

## Auditing shadow mode

In shadow the program entities show the list the feature would write, and
the last write entity shows each write it would have made. Compare the
program with the device's own Reservation Schedule sensor: In Sync is off
whenever they differ, which in shadow means the plan has entries of its own.
The acknowledgement shows each segment's status, when its entry fires, and
any warning.

The feature also reports people's changes in shadow: a setpoint or mode
change that no entry on the device explains, an entry added to the list, or
the reservation switch turned off. It never undoes them.

## Behaviour in brief

- **Every write is a possible start.** Outside a TOU window, a setpoint left
  above the upper tank started the compressor within about 30 seconds in 112
  of 117 writes, whether it came from an entry or directly.
- **An entry's mode does not take effect inside a TOU window**; its setpoint
  does. The mode is held, and applied when the window ends (device test 6).
  A segment that changes the mode inside the day's highest-priced TOU
  period gets the warning `mode_in_tou_window`.
- **The device fires an entry whatever the compressor is doing.** Cycle
  policy, such as a minimum run before stopping, is the scheduler's: it
  chooses segment times.
- **While Vacation is active, or an Anti-Legionella cycle is running**, the
  feature does not write the list. The device skips entries during Vacation,
  and the feature re-asserts the segment in force when it ends.
- **Power-off is different: entries still fire, and power the heater back
  on.** So when the heater is switched off, the feature switches its own
  entries off by their own flag, and back on when power returns, re-asserting
  the segment in force. This needs Home Assistant running when the heater is
  switched off; otherwise the next entry turns it back on.
- **Unload and restart write nothing.** The programmed entries keep running.

Device behaviour these rules rest on is documented in `nwp500-python`:
*What starts a recovery*, *The TOU recovery cap*, and *Reservations and mode
writes during a TOU window* in the scheduling how-to.

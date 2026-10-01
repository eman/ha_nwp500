# External control (protocol 1)

An optional feature that lets an external scheduler control the NWP500
through Home Assistant. The scheduler publishes a **plan**: a timeline of the
states the heater should be in. The feature programs that plan into the
heater's own weekly reservation list, so the heater carries it out itself and
keeps following it if Home Assistant, the feature or the scheduler becomes
unavailable.

The feature is off by default, and needs a scheduler of your own: an
automation, a Node-RED flow or a script. Enabling it starts in **Preview**
(`shadow` in the protocol), which works out and reports the reservation list
it would write and writes nothing to the heater. **Live** writes the list;
choosing it first shows your own settings for confirmation, which
**Stopped** (`disabled`) restores. Live mode was cut over in stages on a real
heater before protocol `1` was declared.

The options form and the entities use plain names; the protocol keeps its
own. A **plan** is the specification's *intent* (its id is `intent_id`), a
**plan step** is a *segment*, and **your own settings** are the *owner's
program*. Raw states and attribute values are the protocol's, whatever the
display shows.

**The feature operates and reports the water heater, and nothing else.** It
reads only the heater, never the home's power, solar, prices or forecasts,
and makes no decision of its own. When to heat, and in which mode, is your
scheduler's. Surplus grants, the assisted mode and mode confirmation from
the compressor and elements were removed for that reason (2026-09-30). The
protocol still lists them, fixed or empty, until
[#192](https://github.com/eman/ha_nwp500/issues/192) removes them.

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
| Plan entity | none | Required. The entity your scheduler puts its plan on; see below |
| Mode | Preview | Stored as the `mode` option (spec section 6.1); see the table below |
| Lowest / highest temperature | the heater's range | Optional tighter bounds. Empty uses the heater's own `dhw_temperature_min` / `max`. A plan's `"min"` setpoint means the lowest |
| Allowed modes | Heat pump, Energy Saver (Eco) | Modes a plan may choose. Heat pump must be allowed for a plan to hold it. When first going live, one mode keeps the heater from switching modes |
| Schedule size limit / entries kept free | 16 / 2 | The most entries the heater's schedule may hold in total, **your own included**, and how many are kept free for changes needed right away. The feature uses what is left after your entries and the ones kept free. Navien documents 16; the heater tested held 32, and larger lists are untested |

| Mode | Stored as | What it does |
|---|---|---|
| Preview: show what the plan would do, write nothing | `shadow` | Works out the schedule and shows it on the entities; writes nothing |
| Live: follow the plan | `live` | Writes the plan into the heater's schedule. The next page shows your own settings; it goes live only once you submit it |
| Stopped: give the heater back your own settings, then do nothing | `disabled` | Hands the heater back once, then does nothing. Its entities stay; turning the toggle off removes them |

Saving the form while already live shows your own settings again, on a page
that says it stays live.

Changing any option updates the capability entity, and so its version.
Options of earlier versions are removed the next time the form is saved: the
live switches, the surplus sensor, threshold and minimum run, and the
faster-recovery mode. An earlier version's `live` with its segments switch
off writes nothing, and shows as Preview, until then.

While the feature is off, nothing of it loads: no imports, listeners,
entities, stored data, timers or writes. Turning it off again removes its
entities and deletes its stored data.

## The plan entity

The scheduler publishes each plan to **any Home Assistant entity**, chosen as
**Plan entity** in the options (the intent entity of the specification):

- Its **state** must change on every new plan. Use the `intent_id`.
- Its **attributes** are the plan, top-level keys as attributes.

The feature listens for the entity's state changes and stores the last
accepted plan. At start-up it uses the newer of the stored plan and the
entity's document, by `issued_at`.

The typical source is an MQTT sensor from discovery, pointed at a retained
topic that carries the plan:

```json
{
  "name": "Water heater plan",
  "unique_id": "water_heater_plan",
  "state_topic": "scheduler/water_heater/plan",
  "value_template": "{{ value_json.intent_id }}",
  "json_attributes_topic": "scheduler/water_heater/plan"
}
```

A REST sensor or a template sensor works the same way. Exclude the plan
entity from the recorder: its attributes are a document, not history.

For a quick test without a scheduler, a state posted to the REST API
(`POST /api/states/sensor.water_heater_plan` with the plan as
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
| `grants` | list | no | Surplus grants: **not supported**. Accepted, and each rejected (`grants_unsupported`); send none |
| any other key | any | no | Opaque. Echoed on the plan entity, for example `plan_id` |

There is no validity period. The last segment holds until a new plan
arrives, so make it a state you are willing to hold indefinitely.

### Segments

A segment gives the heater's state from its `start` until the next segment's
`start`. Segments must be in increasing order of `start`, which is truncated
to the minute.

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique in the plan |
| `start` | yes | ISO 8601 with offset |
| `setpoint_f`, `setpoint_c` or `setpoint: "min"` | exactly one | The setpoint. Numbers are quantised to half a degree Celsius. `"min"` is the **Lowest temperature** option, else the device's minimum |
| `mode` | on the first segment | `heat_pump`, `energy_saver`, `high_demand` or `electric`. A later segment without one keeps the previous mode |
| `reassert` | no | Protocol 1.1. `true` gives the segment its own entry even when it repeats the state before it, so a person's change is ended at its start |

`vacation` and `power_off` are never accepted. Entries are skipped during
Vacation, so the plan's next entry would never end it. Whether an entry with
the power-off mode powers the heater off is untested, and the mode command
with that value switched the unit tested to Energy Saver (#160). Use
`setpoint: "min"` for effectively off.

### Surplus grants

Not supported: the feature reads nothing about the home's power. A plan's
grants are rejected on their own, `grants_unsupported`, and its segments are
programmed as usual. Store surplus energy with ordinary segments instead.

### Validation

A plan is **rejected whole**, and the plan in force stays, for:
`invalid_document`, `unsupported_protocol`, `duplicate_id`,
`unordered_segments`, `out_of_bounds` (a segment's setpoint),
`mode_not_allowed` or `superseded`. One bad segment rejects the plan, because
skipping it would leave the segment before it in force over its time.

Every **grant** is rejected on its own, `grants_unsupported`.

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
  at least two minutes ahead: a segment already begun, and re-asserting the
  segment after Vacation or power-off.
- **Horizon.** Entries are programmed at most 144 hours ahead, because a
  weekly entry cannot say which week. Later segments are `scheduled` and
  programmed as time passes.
- **Budget.** Entries fit within the **Schedule size limit**, minus every
  other entry on the device, minus those **kept free**. Segments that do not
  fit are `scheduled` and programmed as earlier entries fire.
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
  setpoint or mode change. It does put back an entry a person deleted:
  a new plan is programmed as it stands (see People's changes below).

## Entities

All belong to the device. Unique ids are `<mac>_control_<key>`, and entity
ids `<domain>.<device>_control_<key>` whatever the display name (spec section
4). The four marked *diagnostic* are under the device's Diagnostic section;
they stay enabled. States and attribute names have display labels, such as
"Preview only" for `shadow`; the raw values below are what a scheduler
reads.

| Entity (key) | State | Attributes |
|---|---|---|
| External control interface (`capabilities`), diagnostic | The declaration's version | The declaration (below) |
| Plan (`intent`) | The `intent_id` in force, or `none` | `issued_at`, `received_at`, `segment_count`, `grant_count`, the opaque keys |
| Plan status (`ack`) | `shadow`, `programmed`, `partly_programmed`, `pending`, `rejected` (with no plan in force) or `none` | `rejected` (the latest rejected document, or `null`; spec section 4.2), `intent_id`, `reason`, `detail`; `segments`, each with `id`, `status`, `reason`, `warnings`, `fires_at`, `in_force`, `mode_confirmed` (always `null`; withdrawn, [#192](https://github.com/eman/ha_nwp500/issues/192)) and its opaque keys; `grants`, each `rejected` with reason `grants_unsupported` (withdrawn, [#192](https://github.com/eman/ha_nwp500/issues/192)) |
| External control schedule fingerprint (`program_hash`), diagnostic | The `schedule_hash` of the list the feature wants on the device | `entry_count`, `entries`: each a device entry marked `owner`, `foreign`, `plan`, `near_term` or `guard`; the feature's own also say what they serve and when they fire (spec section 4.2) |
| Plan written to heater (`in_sync`) | On when the device's list hashes the same as the program; off in Preview whenever the plan has entries of its own | `device_hash`, `read_at` |
| Plan written up to (`programmed_until`) | How far the device's copy of the plan reaches | `complete`, `scheduled` |
| Plan next change (`next_entry`) | When the next feature entry fires | `mode`, `setpoint_f`, `setpoint_c`, `kind`, `serves` |
| Plan mode now, Plan temperature now (`wanted_mode`, `wanted_setpoint`) | The state the plan puts the heater in now; a segment a person's deletion kept out does not count (spec section 5.10) | `segment` (the segment in force), `grant` (always `null`) |
| Surplus raise active (`grant_raised`) | Always off (withdrawn, [#192](https://github.com/eman/ha_nwp500/issues/192)) | `grant`, `raised_at`, `setpoint_f`, all `null` |
| External control last write (`last_write`), diagnostic | When the list was last written | `reason`, `added`, `removed` (each entry with `kind`, `serves`, `fires_at`, its mode and setpoint, spec section 4.2), `added_count`, `removed_count`, `truncated` (a list left out to stay within the recorder's size limit), `confirmed`, `simulated`, `owner_state`. Examples: `docs/examples/last-write-*.json` |
| Manual change detected (`override`) | On while a person's change is reported | `field`, `value`, `detected_at`, `segment` (the latest), `reports` (every change in force, not a history; how long each lasts is in spec section 4.2), `report_count`, `truncated` |
| External control heartbeat (`heartbeat`), diagnostic | Updated at least every 15 minutes | none |
| Stop external control (`disable`, button) | | Sets Mode to Stopped. Pressed again while Stopped, it retries a hand-back that failed |

Segment statuses are `shadow`, `scheduled`, `merged` and `ended` in shadow;
live adds `pending`, `programmed`, `in_force`, `failed` and `removed`.
Every grant is `rejected`. The last write's `reason` is one of `plan`,
`cleanup`, `near_term`, `precedence_exit`, `power_off`, `takeover` and
`disable`.

### The capability declaration

| Attribute | Meaning |
|---|---|
| `protocols`, `protocol_versions`, `feature_version`, `mode`, `live` | What runs. `protocol_versions` names the newest minor of each major, `["1.2", "0"]`; a scheduler checks it before relying on `reassert` (1.1). `live` is `{"segments": <the mode is live>, "grants": false}` (withdrawn, [#192](https://github.com/eman/ha_nwp500/issues/192)) |
| `setpoint_min_f` / `_c`, `setpoint_max_f` / `_c`, `setpoint_resolution_c` | The bounds, and the device's half-degree resolution. Absent until the device's feature data has arrived |
| `allowed_modes` | The modes a segment may use |
| `assisted_mode` | Always `energy_saver` (withdrawn, [#192](https://github.com/eman/ha_nwp500/issues/192): the scheduler's choice, not the heater's) |
| `horizon_h`, `near_term_lead_min`, `entry_limit`, `entry_reserve`, `entries_available` | How entries are budgeted. `entries_available` changes as entries fire, so it is left out of the version |
| `grants_supported`, `grant_rules`, `grant_rule_ranges` | `false`, and fixed values nothing follows (withdrawn, [#192](https://github.com/eman/ha_nwp500/issues/192)) |
| `owner_program` | What disabling restores: `declared`, `mode`, `setpoint_f`, `setpoint_c`, `reservations_enabled`, `entries`. `declared: false` is the provisional snapshot shadow uses |
| `entry_mode_in_tou_window`, `entries_fire_when_powered_off`, `entries_fire_in_vacation` | How the heater treats reservation entries, measured on the unit tested |
| `lower_trigger_f`, `setpoint_write_starts_recovery`, `setpoint_write_stops_compressor`, `list_write_starts_recovery`, `unchanged_entry_starts_recovery` | Tank and recovery behaviour measured on the unit tested (withdrawn, [#192](https://github.com/eman/ha_nwp500/issues/192): tank modelling belongs in time_to_heat) |
| `telemetry` | Entity ids for this heater's delivery temperature, compressor and power draw; and the delivery-temperature dip (withdrawn, [#192](https://github.com/eman/ha_nwp500/issues/192)) |

## Live mode

Live mode writes the plan into the heater's reservation list.

- **Going live.** Choosing Live shows your own settings for confirmation:
  the heater's mode, temperature, schedule switch and schedule entries. That
  is the owner's program, which Stopped restores. Live does not run without
  it.
- **Taking the list over.** The first write, once a plan is in force, turns
  the reservation switch on and switches each of the owner's entries off by
  its own flag. The entries stay on the heater.
- **Every write** reads the list first, keeps entries the feature does not
  own, writes the list whole, and counts only once the heater reads back the
  new list. An unconfirmed write is retried once after a minute; after that
  its segments are `failed` with reason `write_not_confirmed`, and
  writing pauses for 15 minutes. Changes that arrive during a write go into
  the next one. A write whose confirmation was lost may still have landed:
  the next read settles which entries are the feature's, so they are never
  mistaken for someone else's.
- **Read-back.** After each entry's minute, plus the poll interval and a
  minute, the heater's setpoint and mode are compared with the entry's. A
  difference makes the segment `failed` with `not_applied_on_device`, unless
  it is a mode held by a TOU window: then it stays `in_force` with
  `held_in_tou_window`. A person's change in the meantime explains any
  difference. Only what the heater reports counts: nothing is inferred from
  what its compressor or elements do. A mode held by a TOU window is checked
  again when the heater reports it or the window ends. Disabling reads its
  direct write back from the heater's status.
- **Statuses** become `pending`, `programmed`, `in_force`, `failed` and
  `removed`, and the acknowledgement's state `programmed`,
  `partly_programmed` or `pending`.
- **People's changes.** An entry a person deletes is not written again
  while its plan is in force. If it would have put a segment in force (its
  plan entry, or the near-term or precedence-exit entry of one already
  begun), that segment is `removed` and never takes effect: the state
  before it holds, and is not written either (spec section 5.10). A new
  plan is the scheduler's answer and is programmed as it stands, so a
  scheduler that honours a deletion leaves that segment out of its next
  plan (spec section 5.6). A person turning the reservation switch off
  keeps it off.
- **Leaving live** for Preview first hands the heater back as disabling does, so shadow starts from the owner's program.
- **Disabling** removes the feature's entries, gives the owner's entries
  their flags and the reservation switch back, then writes the owner's
  state: the one set by the owner's latest enabled entry, else the declared
  mode and setpoint. That direct write is skipped in Vacation or power-off.
  A failed hand-back is retried once after a minute, then at the next start.
  Until it succeeds, a Repairs issue says so and how to finish it, even
  after the feature is turned off. Handing back works even with live mode
  switched off in code, so a heater left holding the feature's entries can
  always be returned.
- **A feature that cannot start** leaves the rest of the integration
  running, and raises a Repairs issue until it starts or is turned off.

## Auditing shadow mode

In Preview (`shadow`) the program entities show the list the feature would
write, and the last write entity shows each write it would have made.
Compare the schedule fingerprint with the device's own Reservation Schedule
sensor: Plan written to heater is off whenever they differ, which in shadow means the plan has entries of its own.
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
- **During Vacation the device skips entries**, so the feature keeps the
  newest plan written on the heater, and re-asserts the segment in force
  when Vacation ends. **During an Anti-Legionella cycle** it writes nothing.
  A plan accepted while the heater is powered off is written when power
  returns.
- **Power-off is different: entries still fire, and power the heater back
  on.** So when the heater is switched off, the feature switches its own
  entries off by their own flag, and back on when power returns, re-asserting
  the segment in force. This needs Home Assistant running when the heater is
  switched off; otherwise the next entry turns it back on.
- **Unload and restart write nothing.** The programmed entries keep running.

Device behaviour these rules rest on is documented in `nwp500-python`:
*What starts a recovery*, *The TOU recovery cap*, and *Reservations and mode
writes during a TOU window* in the scheduling how-to.

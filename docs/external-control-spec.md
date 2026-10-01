# External control (protocol 1): the reservation list as the interface

An **optional** feature that lets an external scheduler control the NWP500
through Home Assistant. The scheduler never calls this integration's services
and does not need to know it exists.

The scheduler publishes a **plan**: a timeline of the states the heater should
be in, covering the whole period it plans for. The feature programs that plan
into the heater's own weekly reservation list, confirms the device holds it,
and reports through entities.

**The reservation list is the interface.** Every change the feature makes to
the heater is a reservation entry, written before its minute. The heater
carries out the plan itself. If Home Assistant, this feature or the scheduler
becomes unavailable, the heater keeps running the programmed entries. There is
no default the heater falls back to inside a plan: the last programmed state
holds until the next entry or the next plan.

**The feature operates and reports the water heater, and nothing else.**
Whether to heat, when, for how long, and in which mode are the scheduler's
decisions. The feature writes the plan and reports what the heater does. It
reads nothing but the heater, and makes no decision of its own.

This document is the complete specification. Everything an implementation
needs is here or in this integration's and `nwp500-python`'s own docs.

- **Protocol version:** `1`, with the compatibility promises of section 1.3.
  Its document format is that of the revised protocol `0`, which replaced a
  first draft that never shipped (section 10). Documents that say `"0"` are
  still accepted and mean the same: they follow protocol 1's schema, as
  amended in section 1.3.
- **Keywords:** MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

---

## 1. Constraints on the integration

### 1.1 Purely additive

1. **Off by default.** An options-flow toggle, *External control*, enables
   the feature. Off is the default for new and
   existing installs.
2. **Nothing loads while it is off.** The feature lives in its own subpackage
   (`custom_components/nwp500/control/`), imported only when enabled. While
   it is off it has no imports, listeners, entities, stored data or timers, and
   makes no writes to the device.
3. **No new `requirements` or `dependencies`** in `manifest.json`.
4. **No change to existing behaviour.** Existing entities, services, unique
   ids, polling and options are untouched. The only change to shared code is
   the options step and a set-up hook.
5. **A regression test.** With the feature disabled, set-up produces exactly
   the entities, listeners and stored data it produced on the release before
   the feature.
6. **Turning it off removes the feature.** Its entities and stored data go,
   even if it had failed to start; deleting the integration removes its
   stored data too. Nothing is written to the heater: the entries the plans
   put there stay, and clearing them is the scheduler's (section 6.6).

### 1.2 Safe when enabled

1. **Enabling starts in `shadow`.** The feature reads the device, plans the
   list it would program, and reports it. It writes nothing to the heater.
2. **`live` is a separate option,** chosen in the options flow.
3. **The dashboard gets only a Disable button** (section 4.4), which stops
   applying plans. Enabling and going live happen in the options flow.
4. **Nothing goes live before the device tests in section 8,** run on
   2026-09-24 and 25.
5. **Proven before promised.** Protocol `0` was experimental. Protocol `1`,
   with the compatibility promises below, followed a staged live cut-over on
   a real heater (section 8.1).

### 1.3 Compatibility (protocol 1)

Within major version `1`:

1. **Documents.** A document valid under `1` stays valid. A minor version
   (`"1.1"`) only adds optional keys; the feature accepts any `1.x`, and a
   key it does not know is kept as opaque and echoed, never an error.
2. **Entities.** The entities of section 4, their unique ids, states and
   attribute names keep their meaning. New entities, attributes, statuses,
   reasons, warnings and last-write reasons may be added. A consumer MUST
   treat a value it does not know as opaque, not as an error.
3. **The capability declaration.** Its keys keep their meaning, and new keys
   may be added. A consumer reads the version to notice a change, and
   `protocol_versions` to know which minor version is implemented. A minor
   version is compared as a number (`1.10` is after `1.9`). A declaration
   without `protocol_versions` implements `1.0`.
4. **Behaviour.** What a document makes the heater do, as sections 5 and 6
   describe it, does not change except to fix a defect, recorded in the
   changelog.
5. **Breaking changes** need protocol `2`. The feature then declares both
   majors in `protocols` and accepts both for at least one release.
6. **Protocol `0`** documents are accepted as `1` for at least one release
   after this one; `protocols` lists `"0"` while they are.

**Amended before release (2026-10-01, #192).** Before any release carried
protocol 1, and with its one consumer changing in step
(eman/dhw-sensor-apps#389), protocol 1 dropped what was not the feature's
job of applying the plan: surplus grants, the assisted mode, the owner's
program, the live switches, the setpoint bounds and allowed modes, the
`"min"` setpoint, the measured tank and recovery facts, the telemetry
entity ids, and the ack's `mode_confirmed`. A document's `grants` key is now
opaque, as any key the feature does not know; a segment's setpoint is a
number.

Minor versions so far: **1.1** adds the segment key `reassert` (section
3.2). **1.2** added a grant's own timing rules; with grants gone it adds
nothing beyond 1.1, and `protocol_versions` keeps listing it.
A feature that only knows an earlier minor version keeps such keys as opaque
ones, so a scheduler relies on them only when `protocol_versions` (section
4.1) lists that minor version or a later one for major `1`. A document may
declare `"1"` or any `"1.x"` either way.

---

## 2. Interface overview

```
scheduler ──► [intent entity] ──► control feature ──whole-list writes──► heater's reservation list ──► heater
                                        │                                  (fires on its own)
                                        ├── near-term entries: a segment already begun, the end of Vacation or power-off
                                        ├── one direct write: when the feature is disabled
                                        └──► entities (section 4): the declaration, the plan and its ack, the program, what happened
```

### 2.1 Input: an intent entity

- An option selects **any entity** as the intent source.
- Its **state** MUST change on every new plan. Use the `intent_id`.
- Its **attributes** are the intent document (section 3), with top-level keys
  as attributes.
- Typical sources, none of which this feature depends on:
  - An **MQTT sensor from discovery**, with
    `value_template: "{{ value_json.intent_id }}"` and
    `json_attributes_topic` pointing at a retained topic that carries the
    document. Home Assistant then keeps the latest plan across restarts.
  - A REST sensor, or a template sensor.
- The feature listens for the entity's `state_changed` events.
- **It stores the last accepted plan** in Home Assistant storage. At start-up,
  the stored plan and the entity's document are compared by `issued_at`, and
  the newer one is used.
- **Recommendation for users:** exclude the intent entity from the recorder.
  Its attributes are a document, not history.

### 2.2 Outputs

Entities (section 4), owned by the device's config entry and prefixed with the
device's name. Key facts are entity **states**, not only attributes, so that
`mqtt_statestream` with `publish_attributes: false`, and history, carry them.

### 2.3 When something is unavailable

| Unavailable | What happens |
|---|---|
| The scheduler stops publishing | The feature keeps programming the stored plan's remaining segments as room frees up. The heater runs the plan to its last segment, and that state holds until a new plan. Nothing is withdrawn. |
| Home Assistant, or this feature | The heater runs the entries already programmed, up to `programmed_until` (section 4.2), then holds the last programmed segment. Entries that have fired are not removed, so after a week the device repeats the programmed run in order. |
| The Navien cloud, or the device's connection | Entries already on the device should fire, since the device runs them locally; this is untested (section 8, test 11). List writes fail and are retried. |
| Home Assistant comes back | The feature reads the device's list and reconciles (section 6.5). It never removes an unfired entry of the stored plan. |

---

## 3. The intent document

### 3.1 Top level

| Key | Type | Required | Meaning |
|---|---|---|---|
| `protocol` | string | yes | `"1"`, or `"1.x"`. `"0"` is still accepted. A document whose major version the feature does not support is rejected (`unsupported_protocol`) |
| `intent_id` | string, at most 64 characters | yes | Unique per plan |
| `issued_at` | ISO 8601 with offset | yes | When the scheduler made it. An older document never replaces a newer one |
| `segments` | list | yes | The timeline (section 3.2). **An empty list stops the plan**: every programmed entry is withdrawn, and the heater keeps the state it is in |
| `commands` | list | no | **Proposed, protocol 1.3.** Device commands, each applied once (section 3.7) |
| any other key | any | no | Opaque. Echoed unchanged on the feature's plan entity, `sensor.<device>_control_intent` (section 4.2), for example `plan_id`, unless it has the name of one of that entity's own attributes (section 4.2), which win |

There is no validity period. A plan's last segment holds until a new plan
arrives, so a scheduler MUST make its last segment a state it is willing to
hold indefinitely.

### 3.2 Segments

A segment gives the heater's state **from its `start` until the next segment's
`start`**. The last segment has no end. Segments MUST be in strictly
increasing order of `start`.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `id` | string, at most 64 characters, unique in the document | yes | Named in acknowledgements |
| `start` | ISO 8601 with offset | yes | Truncated to the minute |
| `setpoint_f` or `setpoint_c` | number | exactly one | The setpoint, converted and quantised to the device's half-degree-Celsius resolution. The heater clamps it to its own range |
| `mode` | string (section 3.4) | on the first segment | The operation mode. A later segment that omits it keeps the previous segment's mode |
| `reassert` | boolean | no | Protocol 1.1. `true` programs the segment's entry even when its state repeats the segment before it (section 5.2), so a person's change (section 5.10) is ended at its start, for example by a nightly segment. An entry that repeats the heater's state starts no recovery (section 8, test 5) |
| any other key | any | no | Opaque, echoed back on the segment's acknowledgement, for example `purpose`, unless it has the name of one of the acknowledgement's own keys (`id`, `status`, `reason`, `warnings`, `fires_at`, `in_force`), which win |

Until the plan's first segment starts, whatever is in force continues: the
segment in force from the previous plan, or the heater's current state.

The scheduler expresses what it wants through setpoints and times. For
example:

- **A charge** is a segment with a high setpoint, followed by a segment with a
  lower one. The heater heats as it would at that setpoint. After the tank
  reaches it, a draw can start the heater again until the next segment.
- **Effectively off** is a setpoint at or below the heater's minimum. The
  setpoint applies even inside a TOU window, and the next segment's entry
  restores normal operation. The heater can still start after a large draw,
  because the lower-tank trigger does not move with the setpoint; it then
  heats only to the setpoint.

Every mode the heater has may be a segment's, `vacation` and `power_off`
included: the feature applies it. What it does is the scheduler's to know.
The heater skips entries during Vacation (section 8), so a plan's later entry
does not end it. Whether an entry with the power-off mode powers the heater
off is untested; the mode command with that value switched the unit tested
to Energy Saver (#160).

### 3.3 Surplus grants (removed)

Removed before release (section 1.3). A document's `grants` key is opaque.

### 3.4 Mode names

| Name | Device mode |
|---|---|
| `heat_pump` | Heat Pump |
| `energy_saver` | Energy Saver (hybrid) |
| `high_demand` | High Demand |
| `electric` | Electric |

### 3.5 Validation

**The document is rejected whole**, with the reason on the acknowledgement
entity (section 4.2), if any of these hold. A rejected document leaves the
plan in force unchanged, and the acknowledgement keeps showing that plan's
statuses, with the rejection beside them.

| Reason | When |
|---|---|
| `invalid_document` | JSON types or required keys are wrong; an id is empty or longer than 64 characters; a setpoint is given in more than one form; the first segment has no mode |
| `unsupported_protocol` | `protocol` is not supported |
| `duplicate_id` | Two segments share an id |
| `unordered_segments` | After truncation to the minute, a segment does not start after the one before it |
| `mode_not_allowed` | A segment's mode is not one the heater has (section 3.4) |
| `superseded` | `issued_at` is earlier than that of the plan in force. A document with the same `issued_at` is accepted |

A segment is part of a timeline, so one bad segment rejects the plan rather
than leaving the previous segment in force over its time.

### 3.6 Example

The heater runs Energy Saver at 139.1 °F. The owner's reservation switch is
off. On Sunday at 05:00:12 the scheduler publishes:

```json
{
  "protocol": "1",
  "intent_id": "i-20261004T0500-7",
  "issued_at": "2026-10-04T05:00:12-07:00",
  "plan_id": "opaque-to-the-feature",
  "segments": [
    {"id": "s1", "start": "2026-10-04T05:00:00-07:00", "setpoint_f": 104.9, "mode": "heat_pump", "purpose": "hold_off"},
    {"id": "s2", "start": "2026-10-04T10:30:00-07:00", "setpoint_f": 140, "purpose": "charge"},
    {"id": "s3", "start": "2026-10-04T14:30:00-07:00", "setpoint_f": 135, "mode": "energy_saver"},
    {"id": "s4", "start": "2026-10-04T22:00:00-07:00", "setpoint_f": 104.9}
  ]
}
```

`s1` has already begun, so it starts through a near-term entry. The feature
writes one list:

| Entry | Mode | Setpoint | Why |
|---|---|---|---|
| Sun 05:03 | Heat Pump | 104.9 °F | `s1`, already begun: the first minute at least two minutes away |
| Sun 10:30 | Heat Pump | 140.0 °F | `s2`; it keeps `s1`'s mode |
| Sun 14:30 | Energy Saver | 134.6 °F | `s3` |
| Sun 22:00 | Energy Saver | 104.9 °F | `s4`, the last segment, which holds until a new plan |

135 °F quantises to 57.0 °C, which reads back as 134.6 °F.

Fired entries are removed with the next write (section 5.3).

If Home Assistant stops at 06:00, the heater still charges at 10:30, moves to
Energy Saver at 14:30 and goes to the minimum at 22:00, holding there until a
new plan is programmed.

### 3.7 Device commands (proposed, protocol 1.3)

**Status: a proposal for review (#196), not implemented.** A plan may carry
device commands besides its segments: settings the heater has that a
reservation entry cannot set. The feature applies each as the scheduler sends
it, with a direct write, and reports what the heater then reports. It decides
nothing: when to send a command is the scheduler's.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `id` | string, at most 64 characters, unique in the document (with the segments' ids) | yes | Named in the acknowledgement |
| `command` | string | yes | One of the table below |
| the command's own keys | | as below | |
| any other key | any | no | Opaque, echoed on the command's acknowledgement |

| `command` | Keys | Library call | Read back from |
|---|---|---|---|
| `vacation` | `days` | `set_vacation_days` | the mode reported as `vacation`, and `vacation_day_setting` |
| `power` | `on`, boolean | `set_power` | the mode: `power_off` or not |
| `anti_legionella` | `enabled`, boolean; `period_days`, with `enabled: true` | `enable_anti_legionella` / `disable_anti_legionella` | `anti_legionella_use` (and its period) |
| `tou` | `enabled`, boolean | `set_tou_enabled` | `tou_status` |
| `demand_response` | `enabled`, boolean | `enable_demand_response` / `disable_demand_response` | `dr_event_status` |

**When a command is applied.** Once, when the plan that carries it is adopted
(section 5.6), in list order, after the plan's list is written. A plan
received again with the same command, same `id` and same content, does not
apply it again; a command whose content changed under the same `id` is
applied again. A restart does not re-apply what was applied. In shadow a
command is evaluated, not written (status `shadow`); while `disabled` nothing
is applied. A plan step can already put the heater in `vacation` or
`power_off` at a time (section 3.4); a command is for now.

**Validation.** A command is checked on its own, and the plan proceeds
whatever happens to it: a bad command never costs the heater its plan. One
whose keys are missing or of the wrong type is `rejected`,
`invalid_command`; an unknown `command` is `rejected`, `unsupported_command`,
so a scheduler can send a command a later version adds. Ranges, such as how
many days of Vacation the heater takes, are the library's to check: a value
it refuses makes the command `failed` with the library's reason. Only
`commands` that is not a list, or an item that is not an object or has no
valid `id`, rejects the document (`invalid_document`).

**A status reports the application, not the setting afterwards.** Once the
heater has reported what a command set, the command is `applied`, and stays
so. If a person later changes that setting, the change is theirs: it is not
undone, and the command is not marked otherwise. A scheduler that wants the
setting back sends the command again, with a new `id` or changed content.

**Open for the owner's decision** (the scheduler side's recommendation in
brackets, eman/dhw-sensor-apps#402):
1. Commands apply only when the plan is adopted, or also take an `at` time?
   [Adoption only: when to send a command is the scheduler's, and timing
   direct writes would give the feature a real-time duty.]
2. Mode and setpoint in segments only, or also as direct commands? [Segments
   only: one channel; a direct write would contend with the segment in
   force.]
3. Recirculation and the air filter now, or when a scheduler needs them?
   [Later; an unknown command is rejected on its own meanwhile.]

---

## 4. Entities the feature creates

All belong to the device. Unique ids are `<mac>_control_<key>`, and entity
ids `<domain>.<device>_control_<key>`, fixed by the key. Display names,
translated state labels, attribute display names and entity categories are
not part of the protocol: a scheduler reads ids, raw states and attribute
keys and values, which are as documented here.

### 4.1 Capabilities: `sensor.<device>_control_capabilities`

- **State:** the declaration's **version**, a short hash of the attributes
  below. It changes whenever the declaration does, so a consumer watching
  states knows to re-read.
- **Attributes:**

| Attribute | Meaning |
|---|---|
| `protocols` | Supported protocol majors, `["1", "0"]` |
| `protocol_versions` | The newest version implemented of each major in `protocols`, in the same order: `["1.2", "0"]`. Every earlier minor of that major is implemented too. A consumer checks it before relying on a minor version's keys |
| `feature_version` | The integration's version |
| `mode` | `shadow`, `live` or `disabled` (section 6.1) |
| `setpoint_resolution_c` | 0.5 on the NWP500, so a model can quantise exactly as the heater does |
| `horizon_h` | How far ahead an entry may be programmed: 144 (section 5.3) |
| `near_term_lead_min` | How far ahead a near-term entry is written: 2 (section 5.2) |
| `entry_limit` | The most entries the feature will use on the device. Option, default **16**. The unit tested accepted and read back a list of 32 (section 8); larger lists are untested |
| `entry_reserve` | Entries kept free for near-term entries. Option, default 2 |
| `entries_available` | `entry_limit` minus every entry on the device and the reserve |

### 4.2 State entities

Each is a **state**, so history and statestream carry it:

| Entity | State | Attributes |
|---|---|---|
| `sensor.<device>_control_intent`, the plan entity (not the input intent entity of section 2.1) | The `intent_id` in force, or `none` | `issued_at`, `received_at`, `segment_count`, the opaque top-level keys |
| `sensor.<device>_control_ack` | `programmed`, `partly_programmed`, `pending`, `rejected`, `shadow` or `none` (below) | `intent_id`; `reason` and `detail`, set only in the `rejected` state; `segments` (below); `rejected` (below) |
| `sensor.<device>_control_program_hash` | The `schedule_hash` of the list the feature wants on the device, comparable with the Reservation Schedule sensor | `entry_count`, `entries`: every entry of that list, as a program item (below) |
| `binary_sensor.<device>_control_in_sync` | On when the device's reservation list hashes the same as the program | `device_hash`, `read_at` |
| `sensor.<device>_control_programmed_until` | Timestamp: the start of the first segment still to be written that is not on the device yet, or of the last segment once all are; unknown without a plan. A `merged` segment, or one a person removed, is not to be written | `complete` (every segment to be written is programmed), `scheduled` (segments waiting for the horizon or for room) |
| `sensor.<device>_control_next_entry` | Timestamp of the next entry the feature owns | `mode`, `setpoint_f`, `setpoint_c`, `kind`, `serves` |
| `sensor.<device>_control_wanted_mode` | The mode the plan puts the heater in now: that of the segment in force, which a person's deletion can keep out (section 5.10), or, with none in force, the state in force when the plan was adopted: what the feature's entries last put in force, or, for a first plan, the heater's own (unknown if it was then in Vacation or powered off) | none |
| `sensor.<device>_control_wanted_setpoint` | The setpoint the plan puts the heater in now, as for the mode, in Home Assistant's unit | `segment` (the segment in force, as the ack's `in_force`) |
| `sensor.<device>_control_last_write` | Timestamp of the last list write | `reason` (`plan`, `cleanup`, `near_term`, `takeover`; a write stored by an earlier version may say `grant_raise`, `grant_lower`, `precedence_exit`, `power_off` or `disable`); `added` and `removed`, lists of entry items (below); `added_count`, `removed_count`, `truncated`, `confirmed`, `simulated` (below) |
| `binary_sensor.<device>_control_override` | On while a person's change is being reported (section 5.10) | The latest report's `field`, `value`, `detected_at` and `segment`; `reports`, every report in force, oldest first; `report_count`, `truncated` (below) |
| `sensor.<device>_control_heartbeat` | Timestamp, updated at least every **15 min** | none |

**Entry items.** Each item of `control_last_write`'s `added` and `removed`
is one of the feature's own entries, which that write put on the device's
list or took off it:

| Key | Type | Meaning |
|---|---|---|
| `kind` | string | What the entry is for: `plan` or `near_term`. An entry an earlier version wrote may be `precedence_exit`, `grant_raise`, `grant_lower` or `guard`, until a write removes it |
| `owner` | string | The program's label for the kind: `plan`, `guard`, or `near_term` for the others |
| `serves` | string | The `id` of the segment it serves; for an earlier version's `grant_raise`, `grant_lower` or `guard` entry, still being removed, the old grant's id. It is the same string as `control_next_entry`'s `serves` |
| `fires_at` | string, ISO 8601 with the local offset | The minute the entry is written for: after any `moved_1_min` shift, and for a near-term entry its near-term minute (section 5.2). A near-term entry confirmed only after its minute never fired; it is issued again, for a later minute, in a later write. An entry left on the device fires again a week later |
| `mode` | string | The mode it sets, as a name (section 3.4) |
| `setpoint_f`, `setpoint_c` | number | The setpoint it sets, to one decimal |
| `setpoint_raw` | integer | The same setpoint as the device holds it, in half-degrees Celsius |
| `enabled` | boolean | False only for the feature's own entries while the heater is powered off (section 5.9) |
| `week`, `hour`, `min` | integer | The device slot, under the device's key names: the weekday bit of `fires_at`'s local date (Monday 64, Tuesday 32, Wednesday 16, Thursday 8, Friday 4, Saturday 2, Sunday 128) and its local hour and minute |

**Program items.** Each item of `control_program_hash`'s `entries` is an
entry of the list the feature wants on the device, with the device's keys:
`enable` (2 on, 1 off), `week`, `hour`, `min`, `mode` (the device's mode id)
and `param`. It adds `owner`: `foreign` for an entry the feature does not
own, kept as read (section 5.1), or the label of one of its own. An
item of the feature's own also carries every key of an entry item, except
that `mode` keeps the device's id, and adds `mode_name`, the mode as a name
(section 3.4). The Reservation Schedule sensor's entries carry display keys
besides the device's, and there `mode_name` is a label such as `Heat
Pump`: compare the two lists by the device's keys.

An entry the feature still wants after a plan replacement (section 5.6) is
kept, not written again, so it is not in a later write's `added`, and its
program item keeps the `serves` and `fires_at` it was added with. A plan
entry is kept only for a segment of the same id, state and minute. An entry
may therefore serve a segment of the plan it was written for,
not of the plan in force.

**Last-write attributes.** `confirmed` is a boolean for the whole write,
since a list is written and confirmed whole (section 5.4). It is `true`
once the device read back the list sent. It is `false` from a write's first
unconfirmed attempt, while its retry is pending (section 5.4); and for a live
write that could not be sent because the list could not be read first. It is
`null` for a simulated write (`simulated` is `true`, in shadow).

**Size.** The recorder keeps none of a state's attributes when they exceed
16 KiB. `added_count` and `removed_count` always give the lists' lengths.
When the attributes would come within 1 KiB of the limit, `removed` is set
to `null`, then `added` if that is not enough, and `truncated` is `true`.
At the default `entry_limit` this does not happen; it can at 32 entries,
when a plan replaces most of its entries. The program entity's `entries`
always lists every entry the feature wants: with ids of at most 64
characters, 32 entries stay well within the limit.
`docs/examples/last-write-*.json` are two writes for
`docs/examples/plan-day.json`, as the entity reports them.

**Override reports.** Each report, and the override entity's own
attributes for the latest, has `field`, `value`, `detected_at` (ISO 8601)
and `segment`:

| `field` | `value` | `segment` | Ends when |
|---|---|---|---|
| `setpoint` | The setpoint found, in half-degrees Celsius | The segment in force, or `null` before the first | An entry on the heater fires after `detected_at` (section 5.10). An entry fires only while the reservation switch is on: one whose minute passes while it is off does not count |
| `mode` | The mode found, as a name (section 3.4), Vacation and power-off included | The segment in force, or `null` before the first | As `setpoint` |
| `removed` | The entry a person removed, as an entry item. Live only | Its `serves`: a segment | The time it would have set is over, by the plan's clock: for a segment's entry, when the next segment starts. Also when a new plan is adopted (section 5.6). A removed entry of the last segment lasts until a new plan, as that segment does. It ends whether or not the heater's status or list can be read |
| `reservations_switched_off` | `false` | `null` | The reservation switch is on again |

The list holds the changes in force, not a history:

- **One report per key.** The key is `field`; for `removed`, `field`,
  `segment` and `value`'s `kind`. A newer report with the same key replaces
  the older one, with a new `detected_at`, as a second setpoint change
  before the next entry fires does.
- **Lifetime.** Each report ends by its own rule, above. Nothing else empties
  the list: a new plan ends only the `removed` reports above, and reports
  are kept across a reload or a restart of Home Assistant.
- **Bound.** At most one `setpoint`, one `mode` and one
  `reservations_switched_off`, and one `removed` per kind
  of entry for each segment of the plan in force whose time is not
  over. `report_count` gives the number. If the attributes would come
  within 1 KiB of the recorder's 16 KiB limit, the oldest reports are left
  out of `reports`, and `truncated` is `true`. The latest is always in the
  entity's own attributes.
- **Off.** The entity is on exactly while a report is in force. Off,
  `reports` is empty, `report_count` is 0, `truncated` is `false`, and the
  other attributes are `null`: nothing from before is kept.
- **Reading every report.** A report can end, or be replaced, between two
  reads. The entity's state is written with each pass that changes the
  list, so a consumer that must see each report records the entity's state
  changes, from history or from state-change events, instead of polling
  it.

**Acknowledgement states:**

| State | Meaning |
|---|---|
| `none` | No plan in force, and no document rejected since start-up |
| `rejected` | No plan in force, and the most recent document was rejected whole (section 3.5). `intent_id`, `reason` and `detail` are that document's, and so is `rejected`; `segments` is empty |
| `shadow` | The plan in force is evaluated but not written: the mode is `shadow` |
| `pending` | Live: at least one segment is `pending` |
| `partly_programmed` | Live: none is `pending`, and at least one is `removed` or `failed` |
| `programmed` | Live: none is `pending`, `removed` or `failed` |

**A rejected document while a plan is in force** does not change the
state, `intent_id` or `segments`: they stay those of the plan in
force, which the rejection leaves unchanged. The rejection is in
`rejected`: `intent_id`, `reason` and `detail` of the most recent rejected
document, or `null`. It is cleared when a document received after it is
accepted: one that arrived earlier and was accepted later, after waiting for
a write in progress, leaves it. The same holds with no plan in force, where
the rejection is also the acknowledgement. So a scheduler knows its
document was rejected when `rejected.intent_id` is that document's, and
accepted when the acknowledgement's `intent_id` is and it was not rejected.

**Commands** (proposed, protocol 1.3) on the ack entity each have `id`,
`command`, `status` and `reason`, and their opaque keys. The status is `shadow`
(evaluated, not written), `pending` (being written), `applied` (the heater
reports it), `failed` (`write_not_confirmed`, or `not_applied_on_device`
when the heater does not report it within the poll interval plus a minute)
or `rejected` (`invalid_command` or `unsupported_command`). The status
reports the application only (section 3.7).

**Segments** on the ack entity each have `id`, `status`, `reason`,
`warnings` (a list), `fires_at` (the minute its entry fires, or will fire
once it is programmed; for a segment already begun, its near-term entry's;
`null` if it has none), `in_force` (whether it is the segment in force now:
a merged segment counts as the one it merged into, and a removed one never
is; when a person's deletion leaves none in force, the state in force when
the plan was adopted holds, section 5.10), and its opaque keys.

| Status | Meaning |
|---|---|
| `shadow` | Evaluated; in shadow, nothing is written |
| `scheduled` | Not yet programmed: beyond the horizon, or waiting for room (section 5.3), with that `reason`. Also, with no reason, a segment starting within `near_term_lead_min`, too close to program: it is asserted by a near-term entry once it begins (section 5.2) |
| `pending` | Being written. A segment that has begun is `pending` until the near-term entry that puts it in force is on the device |
| `programmed` | Its entry is confirmed on the device. A segment that has begun is `programmed` while that near-term entry has not fired |
| `merged` | It sets the same state as the segment before it, so it needs no entry. It goes with that segment: if a person removed its entry, the merged segment does not take effect either |
| `in_force` | It has started and read-back matches (section 5.11) |
| `ended` | A later segment has taken effect, or it had ended when the plan arrived. A `merged` segment, or one a person removed, leaves the one before it in force |
| `failed` | A list write or read-back failed after its retry |
| `removed` | A person removed the entry that puts it in force (section 5.10): its plan entry, or, for a segment already begun, its near-term entry. It never takes effect: the state before it holds over its time, and over any merged segments that follow it |

A segment's `reason`, when it has one:

| Reason | Meaning |
|---|---|
| `beyond_horizon` | `scheduled`: it starts `horizon_h` or more ahead (section 5.3) |
| `entry_budget` | `scheduled`: no room within `entry_limit` (section 5.3) |
| `write_not_confirmed` | `failed`: a write and its retry were not confirmed (section 5.4) |
| `not_applied_on_device` | `failed`: read-back found the device's state differs (section 5.11) |
| `held_in_tou_window` | `in_force`: its mode is held by a TOU window (section 5.8) |

Its `warnings`: `moved_1_min` (its entry moved past an occupied minute,
section 5.2) and `mode_in_tou_window` (it changes the mode inside a TOU
period, section 5.8).

**In shadow,** the program and wanted entities show what the feature would
program. `in_sync` compares that program with the device, so it is off
whenever the program differs from the device's list. That comparison is the
audit.

### 4.3 Entities it reads (existing)

| Status field or entity | Used for |
|---|---|
| The Reservation Schedule sensor (`entries`, `enabled`, `schedule_hash`) | The entries already on the heater, and read-back of every list write |
| `dhw_target_temperature_setting` (water heater target, target-temperature number) | Setpoint read-back after each entry |
| `dhw_operation_setting` (water heater operation mode) | Mode read-back after each entry |
| `tou_status`, the TOU schedule | Flagging a mode change inside a TOU window (section 5.8) |

### 4.4 Controls

`button.<device>_control_disable` switches the feature to `disabled`: it
stops applying plans (section 6.6). Nothing on the dashboard switches it to
`live`.

---

## 5. The program

### 5.1 Entries that are not the feature's

The feature owns only the entries it writes. Every other entry on the
heater, the owner's or anyone's, is kept exactly as read: not switched off,
not restored, and counted against `entry_limit`. While live, the first write
turns the reservation switch on, so the plan's entries fire. The feature
keeps no copy of the owner's program; what the heater runs besides the plan
is the scheduler's to plan around.

### 5.2 From plan to entries

1. **One entry per segment.** Each segment's start becomes one entry carrying
   the segment's mode and setpoint. A device entry always sets both.
2. **Merged segments.** A segment whose mode and setpoint equal those of the
   segment before it gets no entry. Its status is `merged`. A segment with
   `reassert: true` is never merged: it gets its own entry.
3. **Weekday and time.** Each entry has the weekday bit of its local date and
   its local hour and minute. The device fires entries in Home Assistant's
   time zone (section 8, test 1).
4. **Near-term entries.** A change that must happen now is written as an entry
   for the first minute that starts at least `near_term_lead_min` (2) minutes
   ahead. This covers a segment already begun when its plan arrives.
5. **No entry for a passed minute.** An entry for a minute that has passed
   would fire a week later. If a write confirms only after its near-term
   entry's minute, the feature checks the read-back. If the heater did not
   change, it removes that entry and writes a new near-term entry.
6. **One enabled entry per slot.** A plan entry never shares a weekday and
   minute with another enabled entry on the device. It moves to the next free
   minute, and its segment gets the warning `moved_1_min`. It may share one
   with an entry switched off by its own flag, such as an owner entry while
   live: the device stores both and fires only the enabled one (section 8).
   Which of two enabled entries in one slot wins is untested.
7. **Ownership.** Every entry the feature writes is recorded in storage as its
   own, with the segment it serves.

### 5.3 Horizon, budget and fired entries

- **Horizon.** An entry MUST fire within **144 hours** of the write. A weekly
  entry cannot express a date: an entry for a minute seven or more days away
  would fire at the next occurrence of its weekday, a week early. 144 hours
  leaves a day's margin.
- **Budget.** The plan's entries fit within `entry_limit`, minus every other
  entry on the device, minus `entry_reserve`. The reserve is kept free for
  near-term entries.
- **In time order.** Segments are programmed in order of `start`, as far as
  the horizon and the budget allow. The rest are `scheduled`, and are
  programmed as earlier entries fire and are removed. `programmed_until`
  reports how far the device's copy of the plan reaches.
- **Fired entries** are removed in the next list write, which also programs
  the next scheduled segment. While the feature is unavailable they stay, and
  repeat a week later (section 2.3).

### 5.4 Writing the list

- **Read first.** Before every write, the feature reads the device's list.
  Entries it does not own are kept as read (section 5.1). The list is held from that read
  through the write, against the integration's own reservation services.
- **Whole-list, confirmed.** The list is written whole with the library's
  confirmed write (`update_reservations_confirmed`), never slot by slot. A
  write counts only once the device reads back the new list: on the unit
  tested, 2 of about 30 writes were lost with no error, and the device kept
  its previous list (section 8).
- **Coalesced.** Changes that arrive while a write is in flight are folded
  into the next write.
- **Retry once.** An unconfirmed write is retried once after 60 s. After that,
  its segments are `failed`, and writing pauses for 15 min before
  it tries again. A near-term entry in an unconfirmed write moves to the
  first minute it can still make after the retry, so the retry never writes
  an entry whose minute has passed. A near-term entry is never dropped unwritten while its segment
  is in force: while writes are paused, or once its minute has passed
  unwritten, it moves forward. Once a later segment is in force, it is
  dropped instead: it would put an ended segment's state back.
- **An unconfirmed write may have landed.** Its confirmation can be lost
  while the device took the list. The next read settles it: if the device
  holds the list sent, the write is committed; otherwise any of its entries
  found on the device are the feature's, not a person's.
- **Taking the list over.** The first live write, once a plan is in force,
  turns the reservation switch on, even if the plan has nothing of its own
  to add yet (reason `takeover`).
- **A missing entry is not restored.** A plan entry missing from the device
  was removed by a person. The feature does not write it again, and its
  segment is `removed`, for as long as that plan is in force, across
  restarts. The plan in force received again (the intent source dropped out
  and came back: the same `intent_id` and `issued_at`) is not adopted again.
  A new plan is the scheduler's answer, and is programmed as it stands
  (section 5.6).
- **Unconfirmed writes are kept.** Every write sent since the last confirmed
  one is kept, the last five, and the next read of the list is settled
  against all of them. A write that was never sent, because the list could
  not be read first, is not kept.
- **A near-term entry confirmed after its minute** never fired, and is issued
  again for the next minute it can make.

### 5.5 Direct writes

None. Every change is an entry, including changes that must happen now
(section 5.2). Device commands (section 3.7, proposed) are the plan's
direct writes: each applied once, as sent.

### 5.6 Replacing a plan

A new accepted plan applies from its receipt:

- **The wanted list is recomputed.** Entries already on the device that the
  new list also wants, with the same weekday, minute, mode and setpoint, are
  kept. The rest of the old plan's unfired entries are removed. Everything
  happens in one write.
- **The segment in force** gets a near-term entry only if it wants a
  different state from the state the feature's entries have in force.
- **A new plan is authoritative.** The feature carries out plans; whether
  to honour or overrule a person is the scheduler's decision, taken with
  the acknowledgement and the override entity in view (section 5.10). So
  what people removed from the plan before is not held against a new plan:
  it is programmed as it stands, `removed` statuses start afresh, and the
  `removed` reports end. The feature keeps a record of what its own entries
  put in force, not of the heater's state:
  - A person's setpoint or mode change is outside that record. A plan
    republished unchanged writes nothing.
  - A person's deletion of the feature's entry means that entry's state
    never took effect. A new plan that still wants that state puts it in
    force. A scheduler that honours the deletion leaves that segment out
    of its next plan, or changes it.
- **An empty `segments` list** withdraws every programmed entry. The heater
  keeps its current state.

### 5.7 Surplus grants (removed)

Removed before release (section 1.3): the feature reads nothing about the
home's power. A heater still holding an earlier version's grant entries has
them removed by the next write.

### 5.8 TOU

Documented in `nwp500-python` `docs/how-to/schedule-operation.rst`,
"Reservations and mode writes during a TOU window":

- **The feature never writes the TOU switch or the TOU schedule.**
- **An entry's mode does not take effect inside a TOU window.** Its setpoint
  does. The mode is held, and applied when the window ends (section 8, test
  6). A low setpoint works in a window. A mode read back
  as `held_in_tou_window` is checked again: applied when the heater reports
  it, which is not a person's change, and `not_applied_on_device` if the
  window ends without it.
- **A segment that changes the mode inside a TOU period** is accepted with the
  warning `mode_in_tou_window`. Its mode is reported unconfirmed until
  read-back confirms it (section 5.11).
- **The TOU recovery cap.** Under TOU, a recovery can stop short of the
  setpoint by design (`nwp500-python`
  `docs/explanation/tou-recovery-cap.rst`). A scheduler should not wait for
  the tank to reach the setpoint.

### 5.9 Vacation, power-off and Anti-Legionella

**No special handling.** The feature applies the plan whatever state the
heater is in, and writes as usual. The heater skips entries during Vacation
and fires them while powered off (section 8). A person putting the heater in
Vacation or powering it off is reported like any other change (section
5.10); what to do about it is the scheduler's.

### 5.10 People's changes

The feature reports people's changes. The scheduler decides.

- **A setpoint or mode change** that no entry explains is a person's, whether
  it was made in the app, on the panel, or through Home Assistant's own
  entities. A change is explained by an entry when it matches the entry's
  state within the poll interval plus one minute after the entry's minute,
  or up to a minute before it (the heater's clock runs a few seconds ahead).
  After Home Assistant or the heater was unreachable, entries that fired
  since the state was last read explain it too. It is reported on the
  override entity, and it lasts until the next entry fires.
- **The device's own TOU-window changes** to `hp_upper_on_temp_setting` are
  thresholds, not the setpoint, and are not a person's change.
- **Changes to the reservation list:**
  - A plan entry a person deletes is not restored (section 5.4). Its segment
    is `removed`, and the segment before it holds over its time.
  - Any other entry of the feature's that a person deletes is not written
    again either, and is reported as `removed` on the override entity.
    Nothing else is written in its place.
  - **One rule for every deletion, within the plan in force.** A segment
    takes effect only through the entry that puts it in force: its plan
    entry, or, for a segment already begun, its near-term entry. A person
    deleting that entry before it fires
    keeps the segment out: it is `removed`, and never takes effect, and
    neither do the merged segments that follow it. The state before it
    holds over its time: the segment before it, or, if that had ended when
    the plan was adopted, the state in force then. The feature never writes
    that state on its own, and everything that reads the segment in force
    reads this one: `in_force`, the wanted entities and read-back. A
    restart keeps it out; a new
    plan is the scheduler's answer (section 5.6).
  - **Whether an entry fired** is judged from the heater's list, not from
    its setpoint and mode, which a raise or a person's change can alter.
    An entry fired if a list read at or after its minute still holds it,
    or once a minute has passed without its deletion being found. Deleting
    an entry that fired is reported, and changes nothing else.
  - A plan entry that a newer plan replaced, still on the heater, is not
    the new plan's segment's. Keeping a segment out also withdraws any
    near-term entry still waiting to be written for it.
  - An entry a person adds is not the feature's: it is kept as read
    (section 5.1), counts against the budget, and fires as the person set
    it.
  - A person turning the reservation switch off stops every entry. It is
    reported as `reservations_switched_off`, and the feature does not turn it
    back on.

### 5.11 Read-back

- **The list.** A segment is `programmed` when the confirmed write's list
  matches the wanted list by canonical form. `in_sync` compares the device's
  `schedule_hash` with the program's on every schedule read.
- **Each entry.** After an entry's minute, the feature compares the device's
  setpoint and mode with the entry's, within the poll interval plus one
  minute. Setpoints are compared within the device's half-degree resolution.
  A setpoint or mode that does not match marks the segment with reason
  `not_applied_on_device`.
- **Only what the heater reports counts.** A mode is applied when the heater
  reports it; nothing is inferred from what its compressor or elements do.
  Inside a TOU window a mode the heater does not report yet is
  `held_in_tou_window`, not a failure. It is applied once the heater reports
  it, and `not_applied_on_device` if the window ends first.

### 5.12 Heartbeat

`sensor.<device>_control_heartbeat` updates at least every 15 min, including
in shadow. That is how a consumer knows the feature is alive.

---

## 6. Lifecycle

### 6.1 Modes

- **`shadow`:** reads the device, validates, plans the list, and updates every
  entity with what it would write. Writes nothing. A segment that would be
  written, or is in force, has status `shadow`; `scheduled`, `merged` and
  `ended` still show.
- **`live`:** writes the list. Options an earlier version stored as `live`
  with its separate segments switch off write nothing, and behave as
  shadow, until the form is saved again.
- **Leaving live** writes nothing: what is on the heater stays.
- **`disabled`:** section 6.6.

### 6.2 Enabling

Turning the toggle on starts the feature in `shadow`.

### 6.3 Going live

Saving the options with `live` goes live. Going live on a heater that holds
nothing of the feature's discards anything shadow simulated, and programs
the plan afresh, asserting the segment in force. The first write turns the
reservation switch on (section 5.1).

### 6.4 Unload and restart

**Stopping Home Assistant or reloading the integration writes nothing.** The
programmed entries keep running on the device.

### 6.5 Start-up

1. Read the device's list.
2. Compare the stored plan with the entity's document by `issued_at`, and take
   the newer.
3. If that is the stored plan, keep its unfired entries. Remove fired entries
   and program `scheduled` segments that now fit, in one write.
4. If it is a newer document, replace the stored plan (section 5.6).
5. A segment whose entry fired during the outage is `in_force`. It is not
   written again. A change of the heater's state since it was last read
   that no entry explains, counting entries that fired meanwhile, is a
   person's change (section 5.10), and is not re-asserted.

Start-up never withdraws a programmed entry because time has passed.

### 6.6 Disabling

Switching to `disabled`, by the Disable button or the options, stops
applying plans: no plan is adopted and nothing is written. What is on the
heater stays there, the plan's entries included; the scheduler's next plan,
or the owner, decides what happens to them. Turning the toggle off does the
same, then removes the entities and the stored data.

---

## 7. Configuration (options flow)

| Option | Default |
|---|---|
| External control enabled | off |
| Mode | `shadow` (Preview), `live` or `disabled` (Stopped) |
| Intent entity | none (required to enable) |
| Entry limit | 16 (the unit tested held 32; section 8) |
| Entry reserve | 2 |

Changing an option updates the capability entity, and so its version.

Options of earlier versions are dropped the next time the form is saved: the
live switches, the surplus entity and threshold, the minimum run before
lowering a raise, the assisted mode, the setpoint bounds, the allowed modes
and the owner's program. Options stored as `live` with the segments switch
off show as Preview, since they write nothing.

---

## 8. Device tests

Run on one NWP500 on 2026-09-24, through the integration's own services,
with the owner's reservation list saved beforehand and restored, confirmed by
read-back, after each test. Each result updates a capability fact or a rule
in this document.

| # | Test | Result |
|---|---|---|
| 1 | **Clock.** An entry fires at its minute in Home Assistant's time zone | **Passed.** Six entries fired at their minute; each change was seen within 5 s |
| 2 | **Near-term entries.** An entry written `near_term_lead_min` ahead fires at its minute | **Passed** for a write confirmed within 5 s. A confirmation arriving after the minute was not exercised |
| 3 | **Entry limit.** The largest list the device confirms | **At least 32.** Lists of 7, 16, 17, 20 and 32 entries were confirmed and read back. Larger lists were not tried. One write, of 12 entries, was lost: no error, and the device kept its previous list |
| 4 | **Writing the list.** Whether a write, with no entry firing, starts a recovery | **No.** 8 writes with the tank 2.2 °F below the setpoint and the compressor off; none started it within 3 minutes |
| 5 | **Unchanged entries.** Whether an entry repeating the heater's mode and setpoint starts a recovery | **No**, in 4 runs with the tank 1.3–2.2 °F below the setpoint, the compressor off and no hot water drawn. That each fired is inferred from tests 1 and 2, since it changes nothing observable |
| 6 | **Entry mode in a TOU window.** Held until the window ends, applied at the end, or discarded | **Held, and applied when the window ends.** An entry at 16:30 set Energy Saver at the heater's setpoint, inside the 16:00–20:59 peak window. The heater stayed in Heat Pump with no change through 20:57. At 21:00:05 it switched to Energy Saver and started a recovery: the upper element ran for about 3 minutes, then the compressor. That recovery may be the window's end rather than the mode change; this run cannot tell them apart |
| 7 | **Vacation and power-off.** Whether entries are skipped, and whether a missed entry runs late | **Vacation: skipped, and not run late** when Vacation ended. **Power-off: not skipped.** In two runs an entry fired while the heater was powered off by the power command and turned it on: once in Heat Pump, and once in Energy Saver, the entry's mode rather than the mode the heater had. Powered off for 6 minutes with no entries, it stayed off, so the entry did it. This contradicts the library's docs |
| 8 | **Anti-Legionella.** Whether an entry firing mid-cycle interrupts it | **Not run: no cycle could be started.** Enabling Anti-Legionella (period 14 days) did not start a cycle within 20 minutes, and nothing else starts one on demand. The owner keeps it off, so no cycle is due. The feature writes nothing while a cycle runs (section 5.9), so the open question only matters to an owner who enables it |
| 9 | **Per-entry enable flag.** Whether an entry with its own flag off is skipped | **Passed.** The switched-off entry was skipped and the next enabled entry fired |
| 10 | **Slots.** Two entries on the same weekday and minute, one switched off | **Accepted.** The device stored both and fired only the enabled one |
| 11 | **Offline.** Whether entries fire while the device is off the cloud, and survive a power cut | **Power cut: the list survives, a missed entry is skipped.** The heater's breaker was switched off for 6 minutes (00:17–00:23) with four entries programmed. On power-up it was back on the cloud in 23 s with the same list, reservation switch, mode and setpoint. The entry that fell during the cut did not run late. The next two fired within 3 s of their minute, so the clock kept time. **Off the cloud: not tested, and out of scope.** Every entry observed fired while the heater was on Navien's cloud, so whether entries or the clock depend on it is unknown. That matters only during an internet outage at the heater, not when Home Assistant or the scheduler is unavailable, which is the case the design must survive |

Also observed:

- **Fired entries stay on the device.** An entry is not removed when it
  fires, so it fires again a week later unless the feature removes it
  (section 5.3).
- **A setpoint entry starts a recovery** when it leaves the tank below the
  new setpoint: the compressor started 35 s after one, as the library reports
  for direct writes.
- **An entry's mode takes effect outside a TOU window.** An entry switched the
  heater from Energy Saver to Heat Pump at its minute.
- **Powering on re-evaluates.** The heater came back in the mode and setpoint
  it had, and once, with the tank below the setpoint, started a recovery 30 s
  later.
- **The mode command with the power-off value does not power the heater
  off.** It switched it to Energy Saver. The power command does power it off,
  and the heater then reports the power-off mode (#160).

All tests that can be run remotely have been run. Test 8 waits for an Anti-Legionella cycle, and the off-cloud half of test 11 is out of scope.

### 8.1 The live trial (the staged cut-over)

Run on the same heater on 2026-09-25, 02:07–03:47, through the options flow
and an intent entity, with the heater's own program as the owner's: Heat
Pump at 141.8 °F, reservations off, one Saturday entry.

| Stage | What was published | Result |
|---|---|---|
| Hand-back | Disabling a live feature that held the heater's list | The feature's entry was removed and the owner's list, switch and state restored; the heater's status read back the owner's state |
| Going live | `live`, segments only, Heat Pump only | The options flow showed the owner's program; the first write turned the switch on and the owner's entry off by its flag |
| 1. Setpoints | Heat Pump at 141.8, 140.0 and 141.8 °F, ten minutes apart | Both entries fired within 4 s of their minute. Each segment went `in_force` with its mode confirmed (compressor, no element). No change was taken for a person's |
| 2. Two modes | Heat Pump, Energy Saver, Heat Pump | The heater changed mode at each entry's minute, outside a TOU window. Energy Saver stayed unconfirmed, as no element ran |
| 3. A surplus grant | A segment at 144.5 °F, a grant to 147.2 °F, the surplus signal on | The near-term entry started the compressor. After 10 minutes of surplus with the compressor running the raise was written, with a guard at the grant's end. With the minimum run set to 0, the raise was lowered by an entry 15 minutes after the surplus ended, and the guard withdrawn |
| 4. Disable | The Disable button | The owner's list came back exactly (same schedule hash), reservations off, and Heat Pump at 141.8 °F read back from the heater |

Every write was confirmed; none was lost. A first run found two defects,
fixed before this one: an entry was removed seconds after it fired, which
cost a write and its read-back; and the heater's clock runs about 4 s ahead
of Home Assistant's, so an entry's change arrived before its minute and was
taken for a person's.

### 8.2 The deletion trial (#171)

Run on the same heater on 2026-09-30, 09:07–12:27 PDT, with main at 88efbfc
(#176–#180), to check section 5.10 on the heater. The owner's program was
the same as in 8.1: Heat Pump at 141.8 °F, reservations off, and one
Saturday entry. The trial went live through the options flow, with live
segments and without grants, and published plans to the intent entity. A
person's deletion was made with the integration's `update_reservations`
service, a list without the entry, and Vacation with `set_away_mode`.
Every stage used Heat Pump only, and both Vacation stages ran with the
compressor idle and the tank full. Times are PDT.

| Stage | What was done | Result |
|---|---|---|
| Going live | `live`, segments only | The first write, a near-term entry re-asserting the segment in force, was not confirmed. Its retry 60 s later was, with the entry moved to the next minute it could make. The switch came on and the owner's entry went off by its flag |
| 1. A deleted near-term entry | A plan whose segment in force (`t1`, 140.0 °F) began a minute before; its near-term entry (09:16) deleted before it fired | `t1` went `removed`, not in force, and the ack `partly_programmed`. Nothing was written again, before or after 09:16. The wanted state stayed at 141.8 °F, the state before the plan, and the heater stayed there. The deletion was reported on the override entity |
| 2. A new plan | The same segments, as a new document | The near-term entry for `t1` was written again (09:21), fired, and the heater went to 140.0 °F: the new plan is the scheduler's answer (5.6). The removal and its report ended |
| 3. A deleted exit, the segment already in force | Vacation on 12:07:45 and off 12:10:51 within segment `t2` (141.8 °F); the exit for `t2` (12:13) deleted | `t2` stayed in force, not `removed`: the exit only re-asserted it. Nothing was written again. The deletion was reported |
| 4. A deleted exit, the segment begun in Vacation | A plan with `v1` (141.8 °F) in force and `v2` (140.0 °F) at 12:20; Vacation on 12:15:07 and off 12:22:03; the exit for `v2` (12:25) deleted | `v2` went `removed` and `v1` back in force. The heater stayed at 141.8 °F, and nothing was written again |
| 5. Hand-back | The Disable button | The owner's list came back: the Saturday entry enabled, reservations off, and Heat Pump at 141.8 °F on the heater |

Eight list writes were sent, the hand-back's included; one confirmation was
lost, and its retry was confirmed. One read-back finding: `v2`'s plan entry,
whose minute passed in Vacation and which the heater skipped, was checked
after Vacation ended and flagged `not_applied_on_device`. Read-back did not
yet leave out an entry skipped in Vacation (#182, since fixed). Here `v2`
was removed afterwards, which its status shows instead.

---

## 9. Out of scope

- **Scheduling, forecasting, pricing or deciding anything.** The feature
  programs a plan; it never makes one, and reacts to nothing but the heater.
- **The home's power:** spare power, solar, the grid, batteries (section
  5.7). The feature reads only the heater.
- **Inferring the heater's state** from its compressor or elements (section
  5.11). The feature reports what the heater reports.
- **Cycle policy:** minimum run times, not stopping a running
  compressor, not reversing within a cycle. The scheduler chooses segment
  times using the facts in section 4.1. The feature cannot enforce such rules
  on segments anyway, because the device fires an entry whatever the
  compressor is doing.
- **The TOU switch and schedule** (section 5.8).
- **MQTT, or any transport.** The intent entity is the interface.
- **Estimating tank physics.** Plans come in temperatures and times.

---

## 10. Changes from the first draft

| First draft | This revision | Why |
|---|---|---|
| Directives as windows over a baseline, with closing entries restoring it | A plan is a timeline of segments covering the whole period. No default inside a plan; the last segment holds until the next plan | Reservations cover the plan's whole period, and the heater never falls back mid-plan |
| Direct writes as a second main channel: mode windows, hold-off starts, surplus raises, the TOU lever | Every change is an entry. A change needed now is a near-term entry two minutes ahead. The only direct write is disabling | The heater must keep running the plan when the service is unavailable |
| `valid_until`; a stale intent is withdrawn, and start-up deletes a stale intent's entries | Removed. Programmed entries run; only a new plan or disabling removes them | A silent scheduler or an outage must not end the schedule |
| A daily-revert entry at 03:00, and re-applying the intent afterwards | Removed | It cut the plan short every day when Home Assistant was down, and was a daily write that could start a recovery |
| Directive types `charge`, `hold_off`, `mode` and `surplus_grant`, with decisions inside the feature: a tank-relative hold setpoint, completion on the compressor stopping, request cycles | Segments with a setpoint and a mode; `setpoint: "min"` for effectively off | The feature actuates; the scheduler decides |
| Surplus grants written directly | Kept, actuated by near-term entries, with a guard entry at the grant's end | A raise stays bounded on the device if Home Assistant stops mid-raise |
| Minimum run before stop, no reversal within a cycle, restores waiting for the compressor | Removed for segments; declared as device facts. Kept only for lowering a surplus raise | A device-side entry fires regardless. The draft also contradicted itself: a charge's end was a hard limit, yet its restore waited |
| An override blocked its field until 03:00, including the reservation list | People's changes are reported, never undone or adopted; they last until the next entry | With the list as the interface, a day-long pause would stop the controller |
| A constant baseline restored after every directive | The owner's program, switched off while live and restored only by disabling | There is no fallback inside a plan |
| Enabling reservations could re-activate disabled owner entries | Owner entries are switched off by their own enable flags while live | The owner's entries must not fire against the plan |
| `vacation` and `power_off` excluded without a reason | Still excluded: entries are skipped in Vacation, and an entry's power-off mode is untested | `"min"` gives an off that the next entry can end |
| The entry's mode unstated | Every entry carries the full state; a segment may inherit the previous mode | A device entry always sets both |
| No limit on how far ahead | A 144-hour horizon, and segments programmed in order as the budget allows, reported by `programmed_until` | A weekly entry fires at its next weekday occurrence, and the device holds few entries |
| Precedence deleted pending entries | In Vacation, entries stay and the segment in force is re-asserted afterwards. At power-off, the feature switches its own entries off, and back on when power returns | The device skips entries in Vacation, but fires them while powered off and turns the heater back on |
| `applied` meant a write read back | `programmed`, `in_force` and `ended`, with read-back after each entry | An entry's effect is only observable when it fires |
| The TOU lever | Removed | Nothing on the device could undo it |
| Surplus grants (kept by the revision), the assisted mode, mode confirmation from the compressor and elements, the separate live switches (2026-09-30, #191) | Removed, and with #192 removed from protocol 1 too, with the declaration's other keys beyond what runs and the entry budget (section 1.3) | The feature operates and reports the water heater, and nothing else. The home's power and the scheduler's preferences are not the heater's |

---

## 11. Delivery

1. **This specification, the JSON Schema and example documents** in `docs/`.
2. **Skeleton:** the options toggle and the disabled-path regression test;
   intake, validation and the stored plan; the capability entity; `shadow` as
   the default mode; the heartbeat; unload without writes. Done.
3. **Shadow programming:** the owner's program; segments into entries, the
   horizon, the budget and near-term entries; reading the list and
   reconciling; surplus grants; the program, in-sync, programmed-until and
   wanted entities; people's changes. This replaces the first draft's shadow
   engine.
4. **The device tests** in section 8. Run on 2026-09-24 and 25. Test 8
   could not start a cycle, and the off-cloud half of test 11 is out of
   scope.
5. **Live list writes** for segments, starting with a single allowed mode;
   then more modes; then grants. Done, and cut over in stages on a real
   heater (section 8.1). `CONTROL_LIVE_AVAILABLE` in `const.py` stays as a
   kill switch. Leaving live, or switching the feature off, tries the
   hand-back once; disabling retries it after a minute and at the next
   start.
6. **Protocol `1`** after the staged live cut-over: declared, with the
   compatibility promises of section 1.3, a JSON Schema
   (`docs/external-control-protocol-1.schema.json`) and the examples.
   Protocol `0` documents are still accepted.
7. **The boundary** (2026-09-30, #191 and the PR after it): the feature
   applies the scheduler's plan and reports the result, nothing else.
   Removed: surplus grants, the assisted mode, mode confirmation from the
   compressor and elements, the separate live switches, the mode and
   setpoint checks, precedence handling, and the owner's program with its
   hand-back. #192 removed them from protocol 1, with the declaration's
   other keys beyond what runs and the entry budget, coordinated with
   eman/dhw-sensor-apps#389 (section 1.3).

Related: #157 (`water_heater` service reports success in two failure cases);
#160 (turning the water heater off switched it to Energy Saver).
Device behaviour this relies on is documented in `nwp500-python`
(`docs/explanation/what-starts-a-recovery.rst`, eman/nwp500-python#147;
`docs/explanation/tou-recovery-cap.rst`; `docs/how-to/schedule-operation.rst`).

# External control (protocol 0, revised): the reservation list as the interface

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

**The feature actuates; it does not decide.** Whether to heat, when, and for how
long are the scheduler's decisions. The feature declares the device's facts
and limits so that the scheduler can make them. The one rule the feature
applies on its own is a surplus grant (section 5.7): a permission the
scheduler gives, with a ceiling, to react to a signal faster than a scheduler
can.

This document is the complete specification. Everything an implementation
needs is here or in this integration's and `nwp500-python`'s own docs.

- **Protocol version:** `0` (experimental). This revision replaces the first
  draft of protocol 0, which never shipped. Section 10 lists the changes.
- **Keywords:** MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

---

## 1. Constraints on the integration

### 1.1 Purely additive

1. **Off by default.** An options-flow toggle, *External control
   (experimental)*, enables the feature. Off is the default for new and
   existing installs.
2. **Nothing loads while it is off.** The feature lives in its own subpackage
   (`custom_components/nwp500/control/`), imported only when enabled. While
   disabled it has no imports, listeners, entities, stored data or timers, and
   makes no writes to the device.
3. **No new `requirements` or `dependencies`** in `manifest.json`.
4. **No change to existing behaviour.** Existing entities, services, unique
   ids, polling and options are untouched. The only change to shared code is
   the options step and a set-up hook.
5. **A regression test.** With the feature disabled, set-up produces exactly
   the entities, listeners and stored data it produced on the release before
   the feature.
6. **Disabling cleans up.** Turning the toggle off removes the feature's
   entries from the device and restores the owner's program (section 6.6),
   removes its entities, and deletes its stored data.

### 1.2 Safe when enabled

1. **Enabling starts in `shadow`.** The feature reads the device, plans the
   list it would program, and reports it. It writes nothing to the heater.
2. **`live` is a separate option,** with its own switches for segments and
   for surplus grants. A cut-over SHOULD start with a single allowed mode, so
   that no entry changes the mode until setpoints have been proven.
3. **The dashboard gets only a Disable button** (section 4.4). Enabling, going
   live and changing bounds happen in the options flow.
4. **Nothing goes live before the remaining device tests in section 8.**
5. **Experimental until proven.** Protocol `0` ships in pre-releases. Protocol
   `1`, with compatibility promises, follows only after a staged live
   cut-over on a real heater.

---

## 2. Interface overview

```
scheduler ──► [intent entity] ──► control feature ──whole-list writes──► heater's reservation list ──► heater
                                        │                                  (fires on its own)
                                        ├── near-term entries: a segment already begun, surplus raises and lowers
                                        ├── one direct write: when the feature is disabled
                                        └──► entities: capabilities, ack, program, in-sync, programmed-until, heartbeat
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
| Home Assistant, or this feature | The heater runs the entries already programmed, up to `programmed_until` (section 4.2), then holds the last programmed segment. A surplus raise in progress ends at its guard entry (section 5.7). Entries that have fired are not removed, so after a week the device repeats the programmed run in order. |
| The Navien cloud, or the device's connection | Entries already on the device should fire, since the device runs them locally; this is untested (section 8, test 11). List writes fail and are retried. |
| Home Assistant comes back | The feature reads the device's list and reconciles (section 6.5). It never removes an unfired entry of the stored plan. |

---

## 3. The intent document

### 3.1 Top level

| Key | Type | Required | Meaning |
|---|---|---|---|
| `protocol` | string | yes | `"0"`. A document whose major version the feature does not support is rejected (`unsupported_protocol`) |
| `intent_id` | string, at most 64 characters | yes | Unique per plan |
| `issued_at` | ISO 8601 with offset | yes | When the scheduler made it. An older document never replaces a newer one |
| `segments` | list | yes | The timeline (section 3.2). **An empty list stops the plan**: every programmed entry is withdrawn, and the heater keeps the state it is in |
| `grants` | list | no | Surplus grants (section 3.3) |
| any other key | any | no | Opaque. Echoed on the acknowledgement entity unchanged, for example `plan_id` |

There is no validity period. A plan's last segment holds until a new plan
arrives, so a scheduler MUST make its last segment a state it is willing to
hold indefinitely.

### 3.2 Segments

A segment gives the heater's state **from its `start` until the next segment's
`start`**. The last segment has no end. Segments MUST be in strictly
increasing order of `start`.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `id` | string, unique in the document | yes | Named in acknowledgements |
| `start` | ISO 8601 with offset | yes | Truncated to the minute |
| `setpoint_f`, `setpoint_c` or `setpoint` | number, number, or `"min"` | exactly one | The setpoint. A number is converted and quantised to the device's half-degree-Celsius resolution. `"min"` is the lowest setpoint the feature will write, `setpoint_min` (section 4.1) |
| `mode` | string (section 3.4) | on the first segment | The operation mode. A later segment that omits it keeps the previous segment's mode |
| any other key | any | no | Opaque, echoed back. For example `purpose` |

Until the plan's first segment starts, whatever is in force continues: the
segment in force from the previous plan, or the heater's current state.

The scheduler expresses what it wants through setpoints and times. For
example:

- **A charge** is a segment with a high setpoint, followed by a segment with a
  lower one. The heater heats as it would at that setpoint. After the tank
  reaches it, a draw can start the heater again until the next segment.
- **Effectively off** is `setpoint: "min"`. The setpoint applies even inside a
  TOU window, and the next segment's entry restores normal operation. The
  heater can still start after a large draw, because the lower-tank trigger
  does not move with the setpoint (`lower_trigger_f` in section 4.1); it then
  heats only to the minimum.
- **An assisted recovery** is a segment in the declared `assisted_mode`.

`vacation` and `power_off` are never accepted as a segment's mode. Entries are
skipped during Vacation (section 8), so the plan's next entry would never fire
to end it. Whether an entry with the power-off mode powers the heater off is
untested, and the mode command with that value switched the unit tested to
Energy Saver instead (#160). Use `setpoint: "min"` instead.

### 3.3 Surplus grants

A grant permits the feature to raise the setpoint while surplus power is
available and the compressor is already running (section 5.7).

| Key | Type | Required | Meaning |
|---|---|---|---|
| `id` | string, unique in the document | yes | Named in acknowledgements |
| `start`, `end` | ISO 8601 with offset | yes | The window. Truncated to the minute; `end` MUST then be later than `start` |
| `max_f` **or** `max_c` | number | yes | The highest setpoint a raise may use, in exactly one unit |
| any other key | any | no | Opaque, echoed back |

### 3.4 Mode names

| Name | Device mode |
|---|---|
| `heat_pump` | Heat Pump |
| `energy_saver` | Energy Saver (hybrid) |
| `high_demand` | High Demand |
| `electric` | Electric |

### 3.5 Validation

**The document is rejected whole**, with the reason on the acknowledgement
entity, if any of these hold. A rejected document leaves the plan in force
unchanged.

| Reason | When |
|---|---|
| `invalid_document` | JSON types or required keys are wrong; a setpoint is given in more than one form; the first segment has no mode |
| `unsupported_protocol` | `protocol` is not supported |
| `duplicate_id` | Two segments or grants share an id |
| `unordered_segments` | After truncation to the minute, a segment does not start after the one before it |
| `out_of_bounds` | A segment's setpoint is outside `setpoint_min`–`setpoint_max` |
| `mode_not_allowed` | A segment's mode is not in `allowed_modes`, or is `vacation` or `power_off` |
| `superseded` | `issued_at` is earlier than that of the plan in force |

A segment is part of a timeline, so one bad segment rejects the plan rather
than leaving the previous segment in force over its time.

**Grants are checked on their own.** A grant that does not fit is rejected,
with a reason, while the plan proceeds:

| Reason | When |
|---|---|
| `grants_unsupported` | No surplus entity is configured |
| `invalid_window` | Its `end` is not later than its `start` |
| `overlapping_grant` | It overlaps another grant |
| `out_of_bounds` | Its maximum is outside `setpoint_min`–`setpoint_max` |
| `in_past` | Its `end` has passed |
| `not_live` | In `live` mode, the grants switch is off. It is evaluated as in shadow |

### 3.6 Example

The heater runs Energy Saver at 139.1 °F. The owner's reservation switch is
off. On Sunday at 05:00:12 the scheduler publishes:

```json
{
  "protocol": "0",
  "intent_id": "i-20261004T0500-7",
  "issued_at": "2026-10-04T05:00:12-07:00",
  "plan_id": "opaque-to-the-feature",
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

`s1` has already begun, so it starts through a near-term entry. The feature
writes one list:

| Entry | Mode | Setpoint | Why |
|---|---|---|---|
| Sun 05:03 | Heat Pump | 104.9 °F | `s1`, already begun: the first minute at least two minutes away |
| Sun 10:30 | Heat Pump | 140.0 °F | `s2`; it keeps `s1`'s mode |
| Sun 14:30 | Energy Saver | 134.6 °F | `s3` |
| Sun 22:00 | Energy Saver | 104.9 °F | `s4`, the last segment, which holds until a new plan |

135 °F quantises to 57.0 °C, which reads back as 134.6 °F.

Later, surplus appears at 11:20 while the compressor is running. After ten
minutes of surplus the feature raises within the grant:

| Time | List change | Why |
|---|---|---|
| 11:30 | Adds Sun 11:32, Heat Pump, 146.3 °F, and a guard entry at Sun 14:00, Heat Pump, 140.0 °F | The raise, and its end at the grant's end |
| 12:40 | Adds Sun 12:42, Heat Pump, 140.0 °F; removes the 14:00 guard and the fired 11:32 entry | The compressor stopped, so the raise is lowered |

If Home Assistant stops at 06:00, the heater still charges at 10:30, moves to
Energy Saver at 14:30 and goes to the minimum at 22:00, holding there until a
new plan is programmed. If it stops at 12:00, mid-raise, the guard entry at
14:00 lowers the setpoint.

---

## 4. Entities the feature creates

All belong to the device. Names are indicative; unique ids are
`<mac>_control_<key>`.

### 4.1 Capabilities: `sensor.<device>_control_capabilities`

- **State:** the declaration's **version**, a short hash of the attributes
  below. It changes whenever the declaration does, so a consumer watching
  states knows to re-read.
- **Attributes:**

| Attribute | Meaning |
|---|---|
| `protocols` | Supported protocol versions, `["0"]` |
| `feature_version` | The integration's version |
| `mode` | `shadow`, `live` or `disabled` (section 6.1) |
| `live` | The live switches: `segments`, `grants` |
| `setpoint_min_f`, `setpoint_max_f` (and `_c`) | The bounds a setpoint must be within. Options, defaulting to the device's own `dhw_temperature_min` / `max`. A user MAY set a tighter floor, for example 120 °F. `"min"` resolves to `setpoint_min` |
| `setpoint_resolution_c` | 0.5 on the NWP500, so a model can quantise exactly as the heater does |
| `allowed_modes` | Modes a segment may use. Option; default `["energy_saver"]` |
| `assisted_mode` | The mode a scheduler should use for faster recovery. Option; default `energy_saver`. It MUST be one of `allowed_modes`. A scheduler reads it instead of naming a mode, so its plans work with other heaters |
| `horizon_h` | How far ahead an entry may be programmed: 144 (section 5.3) |
| `near_term_lead_min` | How far ahead a near-term entry is written: 2 (section 5.2) |
| `entry_limit` | The most entries the feature will use on the device. Option, default **16**. The unit tested accepted and read back a list of 32 (section 8); larger lists are untested |
| `entry_reserve` | Entries kept free for near-term entries and surplus raises. Option, default 2 |
| `entries_available` | `entry_limit` minus every entry on the device and the reserve |
| `grants_supported` | Whether a surplus entity is configured |
| `grant_rules` | `surplus_on_before_raise_min` (10), `surplus_off_before_lower_min` (15), `min_run_before_lower_min` (option, default 120) |
| `owner_program` | What disabling restores (section 5.1): `declared` (false while provisional), `mode`, `setpoint_f`, `setpoint_c`, `reservations_enabled`, and `entries`, the owner's own entries |
| `lower_trigger_f` | The lower-tank turn-on temperature, which does not follow the setpoint: 104.9 on the unit measured. A low setpoint cannot prevent this trigger |
| `setpoint_write_starts_recovery` | True on the NWP500. Outside a TOU window, a setpoint left above the upper tank started the compressor within about 30 s in 112 of 117 writes, whether the write came from an entry or directly |
| `setpoint_write_stops_compressor` | True on the NWP500. A setpoint lowered well below the upper tank stopped a running compressor within 5 s |
| `entry_mode_in_tou_window` | `held` on the NWP500: an entry's mode does not take effect inside a TOU window, while its setpoint does (section 5.8) |
| `list_write_starts_recovery` | False on the NWP500. Writing the list, with no entry firing, started no recovery in 8 writes, with the tank below the setpoint (section 8) |
| `unchanged_entry_starts_recovery` | False on the NWP500: an entry repeating the heater's mode and setpoint started nothing in 4 runs, with the tank below the setpoint (section 8) |
| `entries_fire_when_powered_off` | True on the NWP500. An entry fires while the heater is powered off, and powers it on in the entry's mode (section 8). The library's docs say otherwise |
| `entries_fire_in_vacation` | False on the NWP500. An entry is skipped during Vacation, and does not run late when Vacation ends (section 8) |
| `telemetry` | Entity ids a consumer can read for this heater: `delivery_temperature` (the upper tank temperature), `compressor_running`, `power`; and `delivery_temperature_dip_f` with `delivery_temperature_dip_min`, the transient dip the delivery-temperature entity shows during a draw without the tank being depleted, which a consumer must not read as depletion (3.4 °F sustained for about 3 minutes on the NWP500's upper probe) |

### 4.2 State entities

Each is a **state**, so history and statestream carry it:

| Entity | State | Attributes |
|---|---|---|
| `sensor.<device>_control_intent` | The `intent_id` in force, or `none` | `issued_at`, `received_at`, the opaque top-level keys |
| `sensor.<device>_control_ack` | `programmed`, `partly_programmed`, `pending`, `rejected`, `shadow` or `none` | `intent_id`; the document-level rejection reason if any; `segments` and `grants`: each with `id`, `status`, `reason`, `warnings`, and its opaque keys |
| `sensor.<device>_control_program_hash` | The `schedule_hash` of the list the feature wants on the device, comparable with the Reservation Schedule sensor | `entry_count`, `entries`: each with its owner (`owner`, `plan`, `near_term` or `guard`), the segment or grant it serves, when it fires, its mode and setpoint |
| `binary_sensor.<device>_control_in_sync` | On when the device's reservation list hashes the same as the program | `device_hash`, `read_at` |
| `sensor.<device>_control_programmed_until` | Timestamp: the start of the first segment not yet on the device, or of the last segment once all are | `complete` (every segment is programmed), `scheduled` (segments waiting for the horizon or for room) |
| `sensor.<device>_control_next_entry` | Timestamp of the next entry the feature owns | `mode`, `setpoint`, `serves` |
| `sensor.<device>_control_wanted_mode` | The mode the plan puts the heater in now | none |
| `sensor.<device>_control_wanted_setpoint` | The setpoint the plan puts the heater in now, including a surplus raise, in Home Assistant's unit | `segment`, `grant` |
| `binary_sensor.<device>_control_grant_raised` | On while a surplus raise is in force | `grant`, `raised_at`, `setpoint` |
| `sensor.<device>_control_last_write` | Timestamp of the last list write | `reason` (`plan`, `cleanup`, `near_term`, `grant_raise`, `grant_lower`, `precedence_exit`, `disable`), `added`, `removed`, `confirmed` |
| `binary_sensor.<device>_control_override` | On while a person's change is being reported (section 5.10) | `field`, `value`, `detected_at`, `segment` |
| `sensor.<device>_control_heartbeat` | Timestamp, updated at least every **15 min** | none |

**Segment statuses** on the ack entity:

| Status | Meaning |
|---|---|
| `shadow` | Evaluated; in shadow, nothing is written |
| `scheduled` | Not yet programmed: beyond the horizon, or waiting for room (section 5.3) |
| `pending` | Being written |
| `programmed` | Its entry is confirmed on the device |
| `merged` | It sets the same state as the segment before it, so it needs no entry |
| `in_force` | It has started and read-back matches (section 5.11) |
| `ended` | The next segment has started |
| `failed` | A list write or read-back failed after its retry |
| `removed` | A person removed its entry on the device (section 5.10) |

**Grant statuses:** `shadow`, `waiting`, `raised`, `ended`, `rejected`,
`failed`.

**In shadow,** the program and wanted entities show what the feature would
program. `in_sync` compares that program with the device, so it is off
whenever the program differs from the device's list. That comparison is the
audit.

### 4.3 Entities it reads (existing)

| Status field or entity | Used for |
|---|---|
| The Reservation Schedule sensor (`entries`, `enabled`, `schedule_hash`) | The owner's entries, and read-back of every list write |
| `dhw_target_temperature_setting` (water heater target, target-temperature number) | Setpoint read-back after each entry |
| `dhw_operation_setting` (water heater operation mode) | Mode read-back after each entry |
| `tank_upper_temperature`, `comp_use`, heat source and element use | Telemetry, surplus grants (section 5.7), and confirmation of a mode (section 5.11) |
| `tou_status`, the TOU schedule | Flagging a mode change inside a TOU window (section 5.8) |
| `anti_legionella_operation_busy`, `vacation_day_setting`, power state | Precedence (section 5.9) |
| The configured surplus entity | Surplus grants |

### 4.4 Controls

`button.<device>_control_disable` switches the feature to `disabled`
(section 6.6). Nothing on the dashboard switches it to `live`.

---

## 5. The program

### 5.1 The owner's program

The **owner's program** is what the heater did before the feature went live:
the owner's own reservation entries, the reservation switch, and the mode and
setpoint the heater held. It is not a fallback inside a plan. It is what
disabling restores.

- **Declared** the first time `live` is chosen, from a snapshot of the device
  that the options flow shows for confirmation (section 6.3). **In shadow**
  the feature uses a provisional snapshot, marked `declared: false`.
- **While live, the plan replaces the owner's program.** The feature turns the
  reservation switch on and turns each owner entry's own enable flag off, so
  none of them fires against the plan. The entries stay on the device, exactly
  as they were apart from that flag, and still count against `entry_limit`.
- **Disabling reverses both** (section 6.6).

### 5.2 From plan to entries

1. **One entry per segment.** Each segment's start becomes one entry carrying
   the segment's mode and setpoint. A device entry always sets both.
2. **Merged segments.** A segment whose mode and setpoint equal those of the
   segment before it gets no entry. Its status is `merged`.
3. **Weekday and time.** Each entry has the weekday bit of its local date and
   its local hour and minute. The device fires entries in Home Assistant's
   time zone (section 8, test 1).
4. **Near-term entries.** A change that must happen now is written as an entry
   for the first minute that starts at least `near_term_lead_min` (2) minutes
   ahead. This covers a segment already begun when its plan arrives, surplus
   raises and lowers, and re-asserting a segment after precedence ends.
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
   own, with the segment or grant it serves.

### 5.3 Horizon, budget and fired entries

- **Horizon.** An entry MUST fire within **144 hours** of the write. A weekly
  entry cannot express a date: an entry for a minute seven or more days away
  would fire at the next occurrence of its weekday, a week early. 144 hours
  leaves a day's margin.
- **Budget.** The plan's entries fit within `entry_limit`, minus every other
  entry on the device, minus `entry_reserve`. The reserve is kept free for
  near-term entries and surplus raises.
- **In time order.** Segments are programmed in order of `start`, as far as
  the horizon and the budget allow. The rest are `scheduled`, and are
  programmed as earlier entries fire and are removed. `programmed_until`
  reports how far the device's copy of the plan reaches.
- **Fired entries** are removed in the next list write, which also programs
  the next scheduled segment. While the feature is unavailable they stay, and
  repeat a week later (section 2.3).

### 5.4 Writing the list

- **Read first.** Before every write, the feature reads the device's list.
  Entries it does not own are kept as read, apart from the owner entries'
  enable flags while live (section 5.1).
- **Whole-list, confirmed.** The list is written whole with the library's
  confirmed write (`update_reservations_confirmed`), never slot by slot. A
  write counts only once the device reads back the new list: on the unit
  tested, 2 of about 30 writes were lost with no error, and the device kept
  its previous list (section 8).
- **Coalesced.** Changes that arrive while a write is in flight are folded
  into the next write.
- **Retry once.** An unconfirmed write is retried once after 60 s. After that,
  its segments or grants are `failed`.
- **A missing entry is not restored.** A plan entry missing from the device
  was removed by a person. It is not written again, and its segment is
  `removed`.

### 5.5 Direct writes

The feature writes the setpoint or mode directly in **one** case: disabling
(section 6.6), a one-off write of the owner's state. Every other change is an
entry, including changes that must happen now (section 5.2).

A direct write uses the library, not the `water_heater` service. A library
`False` or exception is a refusal, reported with its reason (see #157).

### 5.6 Replacing a plan

A new accepted plan applies from its receipt:

- **The wanted list is recomputed.** Entries already on the device that the
  new list also wants, with the same weekday, minute, mode and setpoint, are
  kept. The rest of the old plan's unfired entries are removed. Everything
  happens in one write.
- **The segment in force** gets a near-term entry only if it wants a
  different state from the segment the old plan had in force. A plan that is
  republished unchanged therefore writes nothing, and does not undo a
  person's change (section 5.10).
- **An empty `segments` list** withdraws every programmed entry. The heater
  keeps its current state.

### 5.7 Surplus grants

A grant is the scheduler's permission, with a ceiling, for the feature to
store surplus energy in a compressor cycle that is already running.

**Requires a configured surplus entity** (option). Without one, grants are
unsupported. It is either:

- a `binary_sensor`, where "on" means surplus; or
- a numeric `sensor` in kW, where surplus means its value is at or above a
  **surplus threshold** option (default: the heat pump's running power,
  0.45 kW).

An `unavailable` or `unknown` state counts as no surplus. A source that
publishes its own validity should go `unknown` when stale.

**Raise.** Within a grant's window, and only while the compressor is already
running and the segment in force is in `heat_pump` mode:

- once surplus has been on for 10 min, raise the setpoint to
  `min(max, setpoint_max)` with a near-term entry;
- at the same time, if no segment starts before the grant's end, add a
  **guard entry** at the grant's end restoring the state of the segment in
  force then;
- raise at most once per compressor cycle;
- never raise to start a cycle.

**Lower** with a near-term entry restoring the segment in force, and remove
the guard entry, when:

- the compressor stops; or
- the compressor has run `min_run_before_lower_min` **and** surplus has been
  off for 15 min.

The device ends a raise on its own when the grant's guard entry fires, or when
the next segment's entry fires. If a raise's near-term entry has not fired
when the conditions end, the feature removes it instead of lowering.

### 5.8 TOU

Documented in `nwp500-python` `docs/how-to/schedule-operation.rst`,
"Reservations and mode writes during a TOU window":

- **The feature never writes the TOU switch or the TOU schedule.**
- **An entry's mode does not take effect inside a TOU window.** Its setpoint
  does. Whether the mode is held until the window ends, or discarded, is
  untested (section 8). A low setpoint, including `"min"`, works in a window.
- **A segment that changes the mode inside a TOU period** is accepted with the
  warning `mode_in_tou_window`. Its mode is reported unconfirmed until
  read-back confirms it (section 5.11).
- **The TOU recovery cap.** Under TOU, a recovery can stop short of the
  setpoint by design (`nwp500-python`
  `docs/explanation/tou-recovery-cap.rst`). A scheduler should not wait for
  the tank to reach the setpoint.

### 5.9 Precedence

**While Vacation or power-off is active, or an Anti-Legionella cycle is
running** (`anti_legionella_operation_busy`), the feature makes no setpoint or
mode writes.

- **Vacation.** The device skips entries during Vacation, and an entry whose
  minute passes is missed, not run late (section 8). The feature does not
  write the list; its entries stay on the device. When Vacation ends, it
  re-asserts the segment in force with a near-term entry (`precedence_exit`).
- **Power-off.** The device does **not** skip entries while powered off. An
  entry fires, and powers the heater back on in the entry's mode (section 8).
  Without intervention, a person who switches the heater off would have it
  switched back on by the plan's next entry. So when the feature sees the
  heater powered off, it turns off its own entries' enable flags, which the
  device honours (section 8). This is the one list write it makes under
  precedence. When power returns, it turns them back on and re-asserts the
  segment in force with a near-term entry (`precedence_exit`). This depends on
  Home Assistant being up when the heater is switched off. If it is not, the
  next entry turns the heater back on.
- **Anti-Legionella.** The feature does not write the list during a cycle.
  Whether an entry firing mid-cycle interrupts the cycle is untested
  (section 8).
- **Vacation and power-off are precedence**, not a person's change to the
  setpoint or mode.

### 5.10 People's changes

The feature reports people's changes. It does not undo them, and it does not
adopt them into the plan. The scheduler decides.

- **A setpoint or mode change** that no entry explains is a person's, whether
  it was made in the app, on the panel, or through Home Assistant's own
  entities. A change is explained by an entry when it matches the entry's
  state within the poll interval plus one minute after the entry's minute. It
  is reported on the override entity, and it lasts until the next entry
  fires.
- **The device's own TOU-window changes** to `hp_upper_on_temp_setting` are
  thresholds, not the setpoint, and are not a person's change.
- **Changes to the reservation list:**
  - A plan entry a person deletes is not restored (section 5.4). Its segment
    is `removed`, and the segment before it holds over its time.
  - An entry a person adds is kept as read and reported as `foreign_entry`.
    It counts against the budget, and it fires as the person set it.
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
  A setpoint that does not match marks the segment or grant with reason
  `not_applied_on_device`.
- **A mode counts as applied only when the heater's behaviour confirms it,**
  not on its read-back alone, because the read-back can be held or masked in
  a TOU window. For a mode that uses an element, confirmation is an element
  running or the reported heat source. For Heat Pump only, it is the mode
  state and no element use. Inside a TOU window an unconfirmed mode is
  reported `held_in_tou_window`, not as a failure.

### 5.12 Heartbeat

`sensor.<device>_control_heartbeat` updates at least every 15 min, including
in shadow. That is how a consumer knows the feature is alive.

---

## 6. Lifecycle

### 6.1 Modes

- **`shadow`:** reads the device, validates, plans the list, and updates every
  entity with what it would write. Writes nothing. Statuses are `shadow`.
- **`live`:** writes the list, for segments and for grants according to their
  live switches. What is not live behaves as in shadow.
- **`disabled`:** section 6.6.

### 6.2 Enabling

Turning the toggle on starts the feature in `shadow` with a provisional owner
program.

### 6.3 Going live

The first time `live` is chosen, the options flow shows a snapshot of the
device for confirmation as the owner's program: the mode, the setpoint, the
reservation switch and the owner's entries. It lists the owner entries that
will be switched off while live (section 5.1).

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
   written again. A device state that differs from the segment in force is
   reported as a person's change, not re-asserted.

Start-up never withdraws a programmed entry because time has passed.

### 6.6 Disabling

Switching to `disabled`, by the Disable button or the options, is a
**one-off, unconditional** clean-up:

1. Remove every entry the feature owns.
2. Restore the owner's reservation switch and the owner entries' own enable
   flags (section 5.1).
3. Write the owner's state now: the state set by the owner's latest enabled
   entry, else the declared mode and setpoint. This is the feature's only
   direct write (section 5.5).
4. Read back, and report on the last write entity.

After that the feature writes nothing until the mode is changed in the
options flow. Turning the toggle off does the same, then removes the entities
and the stored data.

---

## 7. Configuration (options flow)

| Option | Default |
|---|---|
| External control enabled | off |
| Mode | `shadow` |
| Live switches: segments, grants | both off |
| Intent entity | none (required to enable) |
| Setpoint min / max | The device's `dhw_temperature_min` / `max` |
| Allowed modes | `energy_saver` |
| Assisted mode | `energy_saver` |
| Entry limit | 16 (the unit tested held 32; section 8) |
| Entry reserve | 2 |
| Surplus entity | none (a `binary_sensor`, or a kW `sensor` with a threshold) |
| Surplus threshold (kW, numeric entity only) | 0.45 |
| Minimum run before lowering a raise | 120 min |
| Owner's program | Declared from a snapshot the first time `live` is chosen (section 6.3) |

Changing an option updates the capability entity, and so its version.

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
| 6 | **Entry mode in a TOU window.** Held until the window ends, applied at the end, or discarded | **Not run yet.** It needs a weekday peak window, 16:00–21:10, free of other testing. The library's docs report the mode held in-window |
| 7 | **Vacation and power-off.** Whether entries are skipped, and whether a missed entry runs late | **Vacation: skipped, and not run late** when Vacation ended. **Power-off: not skipped.** In two runs an entry fired while the heater was powered off by the power command and turned it on: once in Heat Pump, and once in Energy Saver, the entry's mode rather than the mode the heater had. Powered off for 6 minutes with no entries, it stayed off, so the entry did it. This contradicts the library's docs |
| 8 | **Anti-Legionella.** Whether an entry firing mid-cycle interrupts it | **Not run.** It needs a cycle to be running |
| 9 | **Per-entry enable flag.** Whether an entry with its own flag off is skipped | **Passed.** The switched-off entry was skipped and the next enabled entry fired |
| 10 | **Slots.** Two entries on the same weekday and minute, one switched off | **Accepted.** The device stored both and fired only the enabled one |
| 11 | **Offline.** Whether entries fire while the device is off the cloud, and survive a power cut | **Not run.** It needs physical access to the network or the breaker |

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

Before any live write, tests 6, 8 and 11 remain.

---

## 9. Out of scope

- **Scheduling, forecasting, pricing or deciding anything.** The feature
  programs a plan; it never makes one. Surplus grants react only within a
  window and a ceiling the scheduler set.
- **Cycle policy** beyond grants: minimum run times, not stopping a running
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

---

## 11. Delivery

1. **This specification, the JSON Schema and example documents** in `docs/`.
2. **Skeleton:** the options toggle and the disabled-path regression test;
   intake, validation and the stored plan; the capability entity; `shadow` as
   the default mode; the heartbeat; unload without writes. This exists on the
   feature branch and is adapted to this revision.
3. **Shadow programming:** the owner's program; segments into entries, the
   horizon, the budget and near-term entries; reading the list and
   reconciling; surplus grants; the program, in-sync, programmed-until and
   wanted entities; people's changes. This replaces the first draft's shadow
   engine.
4. **The device tests** in section 8. Most were run on 2026-09-24; tests 6,
   8 and 11 remain.
5. **Live list writes** for segments, starting with a single allowed mode;
   then more modes; then grants.
6. **Protocol `1`** after a staged live cut-over.

Related: #157 (`water_heater` service reports success in two failure cases);
#160 (turning the water heater off switched it to Energy Saver).
Device behaviour this relies on is documented in `nwp500-python`
(`docs/explanation/what-starts-a-recovery.rst`, eman/nwp500-python#147;
`docs/explanation/tou-recovery-cap.rst`; `docs/how-to/schedule-operation.rst`).

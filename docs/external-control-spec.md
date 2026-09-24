# External control (protocol 0, revised): the reservation list as the interface

An **optional** feature that lets an external scheduler control the NWP500
through Home Assistant. The scheduler never calls this integration's services
and does not need to know it exists.

The scheduler publishes a **control intent**: what state the heater should be
in, and when. The feature programs that plan into the heater's own weekly
reservation list, confirms the device holds it, and reports through entities.

**The reservation list is the interface.** Every change the feature makes is a
reservation entry written before its time, and every change has an entry that
ends it. The heater carries out the plan itself. If Home Assistant, this
feature or the scheduler becomes unavailable, the heater keeps running the
programmed entries, and every state the feature set still ends on schedule.

**The feature actuates; it does not decide.** Whether to heat, when, and for how
long are the scheduler's decisions. The feature declares the device's facts
and limits so that the scheduler can make them.

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
   entries from the device (section 6.6), removes its entities, and deletes
   its stored data.

### 1.2 Safe when enabled

1. **Enabling starts in `shadow`.** The feature reads the device, plans the
   list it would program, and reports it. It writes nothing to the heater.
2. **`live` is a separate option,** and each field the feature can set
   (`setpoint`, `mode`) has its own live switch.
3. **The dashboard gets only a Disable button** (section 4.4). Enabling, going
   live and changing bounds happen in the options flow.
4. **Nothing goes live before the device tests in section 8.**
5. **Experimental until proven.** Protocol `0` ships in pre-releases. Protocol
   `1`, with compatibility promises, follows only after a staged live
   cut-over on a real heater.

---

## 2. Interface overview

```
scheduler ──► [intent entity] ──► control feature ──whole-list writes──► heater's reservation list ──► heater
                                        │                                  (fires on its own)
                                        ├── direct writes: only for a window already begun,
                                        │   and only once its end entry is on the device
                                        └──► entities: capabilities, ack, program, in-sync, heartbeat
```

### 2.1 Input: an intent entity

- An option selects **any entity** as the intent source.
- Its **state** MUST change on every new intent. Use the `intent_id`.
- Its **attributes** are the intent document (section 3), with top-level keys
  as attributes.
- Typical sources, none of which this feature depends on:
  - An **MQTT sensor from discovery**, with
    `value_template: "{{ value_json.intent_id }}"` and
    `json_attributes_topic` pointing at a retained topic that carries the
    document. Home Assistant then keeps the latest intent across restarts.
  - A REST sensor, or a template sensor.
- The feature listens for the entity's `state_changed` events.
- **It stores the last accepted intent** in Home Assistant storage. At
  start-up, the stored intent and the entity's document are compared by
  `issued_at`, and the newer one is used.
- **Recommendation for users:** exclude the intent entity from the recorder.
  Its attributes are a document, not history.

### 2.2 Outputs

Entities (section 4), owned by the device's config entry and prefixed with the
device's name. Key facts are entity **states**, not only attributes, so that
`mqtt_statestream` with `publish_attributes: false`, and history, carry them.

### 2.3 When something is unavailable

| Unavailable | What happens |
|---|---|
| The scheduler stops publishing | The heater runs the programmed entries to the end of the last window, then stays on the owner's program. Nothing is withdrawn. |
| Home Assistant, or this feature | The same. Entries that have fired are not removed, so after a week the device repeats them. They repeat in pairs, so each repeated state still ends at its repeated end entry. |
| The Navien cloud, or the device's connection | Entries already on the device fire, since the device runs them locally (section 8 confirms this). List writes fail. New directives stay `pending` and are retried. |
| Home Assistant comes back | The feature reads the device's list and reconciles (section 6.5). It never removes an unfired entry of the stored intent. |

---

## 3. The intent document

### 3.1 Top level

| Key | Type | Required | Meaning |
|---|---|---|---|
| `protocol` | string | yes | `"0"`. A document whose major version the feature does not support is rejected (`unsupported_protocol`) |
| `intent_id` | string, at most 64 characters | yes | Unique per intent |
| `issued_at` | ISO 8601 with offset | yes | When the scheduler made it. Orders intents: an older document never replaces a newer one |
| `directives` | list | yes | Ordered by `start`. **An empty list is an explicit "no intent"**: every programmed window is withdrawn and the owner's program applies |
| any other key | any | no | Opaque. Echoed on the acknowledgement entity unchanged, for example `plan_id` |

There is no validity period. A plan ends when its last window ends. A
scheduler that wants its plan to stop sooner publishes a shorter plan.

### 3.2 Directives

A directive is a **window with a state**: from `start` to `end`, the heater's
setpoint, mode, or both, are as given. Outside every window the owner's
program applies (section 5.1).

| Key | Type | Required | Meaning |
|---|---|---|---|
| `id` | string, unique in the intent | yes | Named in acknowledgements |
| `start`, `end` | ISO 8601 with offset | yes | The window. Both are truncated to the minute, and `end` MUST then be later than `start` |
| `setpoint_f` **or** `setpoint_c` | number | one of these, `mode`, or both | The setpoint for the window, in exactly one unit. The feature converts it and quantises it to the device's half-degree-Celsius resolution |
| `mode` | string (section 3.4) | | The operation mode for the window |
| any other key | any | no | Opaque, echoed back. For example `purpose` |

The scheduler expresses what it wants through the setpoint and the window.
For example:

- **A charge** is a setpoint window: `setpoint_f: 140` from 10:30 to 14:30.
  The heater heats as it would at that setpoint. After the tank reaches it, a
  draw can start the heater again until the window ends. A scheduler that
  wants the heater to stop sooner chooses a shorter window.
- **A hold-off** is a setpoint window below the upper-tank temperature. The
  scheduler reads that temperature from the telemetry entities. A lowered
  setpoint prevents only the upper-tank trigger. The lower-tank trigger does
  not move with the setpoint (`lower_trigger_f` in section 4.1), so a large
  draw can still start the heater.
- **An assisted recovery** is a mode window using the declared
  `assisted_mode`.

### 3.3 Validation

**The document is rejected whole**, with the reason on the acknowledgement
entity, if any of these hold:

| Reason | When |
|---|---|
| `invalid_document` | JSON types or required keys are wrong, a directive has neither a setpoint nor a mode, or a setpoint is given in both units |
| `unsupported_protocol` | `protocol` is not supported |
| `duplicate_directive_id` | Two directives share an id |
| `invalid_window` | After truncation to the minute, a directive's `end` is not later than its `start` |
| `overlapping_field` | Two directives that set the same field overlap. A setpoint window may overlap a mode window |
| `superseded` | `issued_at` is earlier than that of the intent in force |

A rejected document leaves the intent in force unchanged.

**Otherwise the document is accepted, and each directive is checked on its
own.** A directive that does not fit is rejected, with a reason, while the
rest proceed:

| Reason | When |
|---|---|
| `out_of_bounds` | Its setpoint is outside `setpoint_min`–`setpoint_max` |
| `mode_not_allowed` | Its mode is not in `allowed_modes` |
| `in_past` | Its `end` has already passed |
| `beyond_horizon` | Its `end` is later than the horizon (section 5.3) |
| `owner_entry_in_window` | One of the owner's enabled entries would fire inside the window (section 5.1) |
| `entry_budget` | Its entries do not fit the budget (section 5.3) |
| `field_not_live` | In `live` mode, it sets a field whose live switch is off. It is evaluated as in shadow |

### 3.4 Mode names

| Name | Device mode |
|---|---|
| `heat_pump` | Heat Pump |
| `energy_saver` | Energy Saver (hybrid) |
| `high_demand` | High Demand |
| `electric` | Electric |

`vacation` and `power_off` are never accepted in a directive.

### 3.5 Example

The owner has no reservation entries of their own, and the heater runs Energy
Saver at 139.1 °F. On Sunday at 05:00:12 the scheduler publishes:

```json
{
  "protocol": "0",
  "intent_id": "i-20261004T0500-7",
  "issued_at": "2026-10-04T05:00:12-07:00",
  "plan_id": "opaque-to-the-feature",
  "directives": [
    {"id": "hold", "start": "2026-10-04T05:00:00-07:00", "end": "2026-10-04T10:30:00-07:00", "setpoint_f": 120, "purpose": "hold_off"},
    {"id": "charge", "start": "2026-10-04T10:30:00-07:00", "end": "2026-10-04T14:30:00-07:00", "setpoint_f": 140, "purpose": "demand"},
    {"id": "boost", "start": "2026-10-04T11:00:00-07:00", "end": "2026-10-04T14:00:00-07:00", "mode": "heat_pump"}
  ]
}
```

The `hold` window has already begun, so the feature programs the other
boundaries, confirms them, and only then writes the hold setpoint directly.

| When | Written as | Mode | Setpoint | Why |
|---|---|---|---|---|
| 05:00:12 + write time | direct write | Energy Saver | 120.2 °F | `hold` has begun |
| Sun 10:30 | entry | Energy Saver | 140.0 °F | `hold` ends and `charge` starts, in one entry |
| Sun 11:00 | entry | Heat Pump | 140.0 °F | `boost` starts |
| Sun 14:00 | entry | Energy Saver | 140.0 °F | `boost` ends |
| Sun 14:30 | entry | Energy Saver | 139.1 °F | `charge` ends; the owner's program applies |

120 °F quantises to 49.0 °C, which reads back as 120.2 °F.

If Home Assistant stops at 06:00, the heater still leaves the hold at 10:30,
runs the charge and the boost, and returns to 139.1 °F at 14:30.

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
| `fields` | The fields a directive may set: `["setpoint", "mode"]` |
| `live_fields` | The fields whose live switch is on |
| `setpoint_min_f`, `setpoint_max_f` (and `_c`) | The bounds a setpoint must be within. Options, defaulting to the device's own `dhw_temperature_min` / `max`. A user MAY set a tighter floor, for example 120 °F |
| `setpoint_resolution_c` | 0.5 on the NWP500, so a model can quantise exactly as the heater does |
| `allowed_modes` | Modes a directive may use. Option; default `["energy_saver"]` |
| `assisted_mode` | The mode a scheduler should use for faster recovery. Option; default `energy_saver`. It MUST be one of `allowed_modes`. A scheduler reads it instead of naming a mode, so its plans work with other heaters |
| `horizon_h` | How far ahead a window may end: 144 (section 5.3) |
| `entry_limit` | The most entries the device holds. Option, default **7** until section 8 measures it; the library's docs say about 16 |
| `entries_available` | `entry_limit` minus the owner's entries and the feature's own entries now on the device |
| `owner_program` | The owner's program (section 5.1): `declared` (false while provisional), `mode`, `setpoint_f`, `setpoint_c`, `reservations_enabled`, and `entries`, the owner's own entries, so a scheduler can plan around them |
| `lower_trigger_f` | The lower-tank turn-on temperature, which does not follow the setpoint: 104.9 on the unit measured. A hold-off cannot prevent this trigger |
| `setpoint_write_starts_recovery` | True on the NWP500. Outside a TOU window, a setpoint left above the upper tank started the compressor within about 30 s in 112 of 117 writes, whether the write came from an entry or directly |
| `setpoint_write_stops_compressor` | True on the NWP500. A setpoint lowered well below the upper tank stopped a running compressor within 5 s |
| `entry_mode_in_tou_window` | `held` on the NWP500: an entry's mode does not take effect inside a TOU window, while its setpoint does (section 5.7) |
| `telemetry` | Entity ids a consumer can read for this heater: `delivery_temperature` (the upper tank temperature), `compressor_running`, `power`; and `delivery_temperature_dip_f` with `delivery_temperature_dip_min`, the transient dip the delivery-temperature entity shows during a draw without the tank being depleted, which a consumer must not read as depletion (3.4 °F sustained for about 3 minutes on the NWP500's upper probe) |

### 4.2 State entities

Each is a **state**, so history and statestream carry it:

| Entity | State | Attributes |
|---|---|---|
| `sensor.<device>_control_intent` | The `intent_id` in force, or `none` | `issued_at`, `received_at`, the opaque top-level keys |
| `sensor.<device>_control_ack` | `programmed`, `partly_programmed`, `pending`, `rejected`, `shadow` or `none`, for the intent in force or the most recent rejected document | `intent_id`; the document-level rejection reason if any; per directive: `id`, `status`, `reason`, `warnings`, and its opaque keys |
| `sensor.<device>_control_program_hash` | The `schedule_hash` of the list the feature wants on the device, comparable with the Reservation Schedule sensor | `entry_count`, `entries`: each with its owner (`owner` or `feature`), its directive ids, when it fires, its mode and setpoint |
| `binary_sensor.<device>_control_in_sync` | On when the device's reservation list hashes the same as the program | `device_hash`, `read_at` |
| `sensor.<device>_control_next_entry` | Timestamp of the next entry the feature owns | `mode`, `setpoint`, `directives` |
| `sensor.<device>_control_wanted_mode` | The mode the program puts the heater in now | none |
| `sensor.<device>_control_wanted_setpoint` | The setpoint the program puts the heater in now, in Home Assistant's unit | none |
| `sensor.<device>_control_last_direct_write` | The reason for the last direct write: `window_begun`, `withdrawn` or `disabled` (section 5.5) | `at`, `mode`, `setpoint`, `matched` (whether read-back matched) |
| `binary_sensor.<device>_control_override` | On while a person's change made inside a window is being reported (section 5.9) | `field`, `value`, `detected_at`, `directive` |
| `sensor.<device>_control_heartbeat` | Timestamp, updated at least every **15 min** | none |

**Directive statuses** on the ack entity:

| Status | Meaning |
|---|---|
| `shadow` | Evaluated; in shadow, nothing is written |
| `pending` | Admitted; its entries are not yet confirmed on the device |
| `programmed` | Its entries are confirmed on the device |
| `in_force` | Its window has begun and read-back matches (section 5.10) |
| `ended` | Its window has ended and the end entry's read-back matched |
| `rejected` | Rejected, with a reason from section 3.3 |
| `failed` | A list write or a read-back failed after its retry |
| `removed` | A person removed one of its entries on the device (section 5.9) |

**In shadow,** the program and wanted entities show what the feature would
program. `in_sync` compares that program with the device, so it is off
whenever the program has entries of its own. That comparison is the audit.

### 4.3 Entities it reads (existing)

| Status field or entity | Used for |
|---|---|
| The Reservation Schedule sensor (`entries`, `enabled`, `schedule_hash`) | The owner's entries, and read-back of every list write |
| `dhw_target_temperature_setting` (water heater target, target-temperature number) | Setpoint read-back at each boundary |
| `dhw_operation_setting` (water heater operation mode) | Mode read-back at each boundary |
| `tank_upper_temperature`, `comp_use`, heat source and element use | Telemetry, and confirmation of a mode (section 5.10) |
| `tou_status`, the TOU schedule | Flagging a mode boundary inside a TOU window (section 5.7) |
| `anti_legionella_operation_busy`, `vacation_day_setting`, power state | Precedence (section 5.8) |

### 4.4 Controls

`button.<device>_control_disable` switches the feature to `disabled`
(section 6.6). Nothing on the dashboard switches it to `live`.

---

## 5. The program

### 5.1 The owner's program

The **owner's program** is what the heater does when the feature has no
window in force: the owner's own reservation entries, as found on the device,
plus a default mode and setpoint.

- **Declared** the first time `live` is chosen, from a snapshot of the device
  that the options flow shows for confirmation. **In shadow** the feature uses
  a provisional snapshot, marked `declared: false`.
- **The owner state now** is what the heater would be in had the feature
  never acted: the latest of the snapshot, the last owner entry that fired,
  and the last change a person made outside a window (section 5.9).
- **The owner state at a future minute** is the state set by the latest
  enabled owner entry that fires between now and that minute. Where none
  fires, it is the owner state now.
- **Owner entries are never rewritten.** Every entry the feature does not own
  is preserved exactly as read. The one exception is the per-entry enable
  flag: if the owner's reservation switch was off, the feature turns the
  switch on for its own entries and turns each owner entry's own flag off, so
  the owner's program behaves as before. Disabling the feature reverses both
  (section 6.6).
- **A window may not contain an owner entry.** An enabled owner entry that
  would fire inside a window, on that weekday, would change the heater
  mid-window. Such a directive is rejected (`owner_entry_in_window`). The
  scheduler sees the owner's entries in the capability entity and can plan
  around them.

### 5.2 From intent to entries

1. **The wanted state over time.** At each minute, the setpoint is that of the
   setpoint window in force, else the owner's. The mode is that of the mode
   window in force, else the owner's.
2. **One entry per boundary.** Every admitted directive's `start` and `end` is
   a boundary. Each boundary becomes one entry carrying the full wanted state
   at that minute: a device entry always sets both a mode and a setpoint.
3. **No entry for no change.** A boundary whose wanted state equals the
   wanted state just before it gets no entry.
4. **The owner's entry wins at an end.** An end boundary on the same weekday
   and minute as an owner entry gets no entry of its own; the owner's entry
   restores the owner's state.
5. **Weekday and time.** Each entry has the weekday bit of its local date and
   its local hour and minute. The feature assumes the device fires entries in
   Home Assistant's time zone until section 8 confirms it.
6. **Ownership.** Every entry the feature writes is recorded in storage as its
   own, with the directives it serves.
7. **All or nothing.** A directive is admitted only if all its boundaries are
   within the horizon and all its new entries fit the budget. A directive is
   never programmed with its start and without its end.
8. **No entry for a passed minute.** An entry for a minute that has passed
   would fire a week later. A boundary less than **2 minutes** away when the
   list is written is treated as begun (section 5.5).

### 5.3 Horizon and budget

- **Horizon.** Every boundary MUST fall within **144 hours** of now. A weekly
  entry cannot express a date: an entry for a minute seven or more days away
  would fire at the next occurrence of its weekday, a week early. 144 hours
  leaves a day's margin. A directive that ends beyond the horizon is rejected
  (`beyond_horizon`), and the scheduler republishes as time passes.
- **Budget.** The feature's entries MUST fit within `entry_limit` minus the
  owner's entries. Directives are admitted in `start` order while their new
  entries fit. The rest are rejected (`entry_budget`). Boundaries shared
  between directives count once.
- **Fired entries** are removed within **24 hours** of firing, batched with
  other list writes where possible. While the feature is unavailable they
  stay, and repeat a week later (section 2.3).

### 5.4 Writing the list

- **Read first.** Before every write, the feature reads the device's list.
  Entries it does not own are the owner's and are kept as read.
- **Whole-list, confirmed.** The list is written whole with the library's
  confirmed write (`update_reservations_confirmed`), never slot by slot.
- **Coalesced.** One write per change of plan. Changes that arrive while a
  write is in flight are folded into the next write.
- **Retry once.** An unconfirmed write is retried once after 60 s. After that
  its directives are `failed`.
- **A missing entry is not restored.** An entry the feature owns that is
  missing from the device was removed by a person. It is not written again,
  and its directive is `removed`.

### 5.5 Direct writes

The feature writes the setpoint or mode directly in exactly three cases, and
only once the device's list confirms the entry that ends the resulting state:

| Reason | When | What is written |
|---|---|---|
| `window_begun` | A window's start has passed on receipt, or passes before its list write confirms | The window's state, once its end entry is confirmed |
| `withdrawn` | A new intent drops a window that is in force | The owner state now, once the list without the window's entries is confirmed |
| `disabled` | The feature is disabled (section 6.6) | The owner state now, if a window is in force |

**There are no other direct writes:** none when time passes, none when a
heat-up completes, none to undo a person's change, none at start-up, and none
to the TOU switch or schedule. Each direct write is a possible recovery start
(`setpoint_write_starts_recovery`), and the feature reports each one on the
last direct write entity.

### 5.6 Replacing an intent

A new accepted intent replaces the old one **directive by directive**:

- **Unchanged** directives keep their entries. If the resulting list is
  unchanged, nothing is written.
- **Changed** directives, those with the same id but a different window or
  state, are treated as dropped and added.
- **Dropped** directives have their entries removed. One that is in force is
  withdrawn with a direct write (section 5.5).
- **New** directives are admitted (section 5.2).

Only a new accepted intent, or disabling the feature, removes programmed
entries.

### 5.7 TOU

Documented in `nwp500-python` `docs/how-to/schedule-operation.rst`,
"Reservations and mode writes during a TOU window":

- **The feature never writes the TOU switch or the TOU schedule.**
- **An entry's mode does not take effect inside a TOU window.** Its setpoint
  does. Whether the mode is held until the window ends, or discarded, is
  untested (section 8).
- **A directive with a mode boundary inside a TOU period** is accepted with a
  warning, `mode_in_tou_window`, on its ack entry. Its mode is reported
  `pending` until read-back confirms it (section 5.10).
- **The TOU recovery cap.** Under TOU, a recovery can stop short of the
  setpoint by design (`nwp500-python`
  `docs/explanation/tou-recovery-cap.rst`). A scheduler should not wait for
  the tank to reach the setpoint.

### 5.8 Precedence

**While Vacation or power-off is active, or an Anti-Legionella cycle is
running** (`anti_legionella_operation_busy`), the feature makes no setpoint or
mode writes and does not rewrite the list. Its entries stay on the device:

- **Vacation** suspends reservations on the device itself, according to the
  library's docs. Section 8 confirms this.
- **Power-off.** Until section 8 shows that entries do not fire during
  power-off, or that an entry's mode does not turn the heater back on, the
  feature turns off its own entries' enable flags when it observes power-off,
  and turns them back on when power-off ends. This is its one list write
  under precedence. It depends on Home Assistant being up when the heater is
  powered off.
- **Anti-Legionella.** Whether an entry firing mid-cycle interrupts the cycle
  is untested (section 8).

### 5.9 People's changes

- **A setpoint or mode change** that no entry explains is a person's, whether
  it was made in the app, on the panel, or through Home Assistant's own
  entities. A change is explained by an entry when it matches the entry's
  state within the poll interval plus one minute after the entry's minute.
  - **Outside every window,** it becomes the owner state now (section 5.1).
    The feature re-programs end entries that restore the owner's state to
    the new value.
  - **Inside a window,** it is reported on the override entity, and nothing
    is rewritten. The window's end entry still fires as programmed. The
    scheduler decides what to do next. The report clears when the window
    ends.
- **The device's own TOU-window changes** to `hp_upper_on_temp_setting` are
  thresholds, not the setpoint, and are not a person's change.
- **A change to the reservation list:** entries a person adds are the owner's.
  A feature entry a person deletes is not restored (section 5.4).
- **Vacation and power-off are precedence** (section 5.8), not a person's
  change to the setpoint or mode.

### 5.10 Read-back

- **The list.** A directive is `programmed` when the confirmed write's list
  matches the wanted list by canonical form. `in_sync` compares the device's
  `schedule_hash` with the program's on every schedule read.
- **Each boundary.** After an entry's minute, the feature compares the
  device's setpoint and mode with the entry's, within the poll interval plus
  one minute. Setpoints are compared within the device's half-degree
  resolution. A setpoint that does not match marks the directive with reason
  `not_applied_on_device`.
- **A mode counts as applied only when the heater's behaviour confirms it,**
  not on its read-back alone, because the read-back can be held or masked in
  a TOU window. For a mode that uses an element, confirmation is an element
  running or the reported heat source. For Heat Pump only, it is the mode
  state and no element use. Inside a TOU window an unconfirmed mode is
  reported `held_in_tou_window`, not as a failure.
- **Direct writes use the library, not the `water_heater` service.** A library
  `False` or exception is a refusal, reported with its reason (see #157).

### 5.11 Heartbeat

`sensor.<device>_control_heartbeat` updates at least every 15 min, including
in shadow. That is how a consumer knows the feature is alive.

---

## 6. Lifecycle

### 6.1 Modes

- **`shadow`:** reads the device, validates, plans the list and the direct
  writes, and updates every entity with what it would write. Writes nothing.
  Directive statuses are `shadow`.
- **`live`:** writes the list and the direct writes, for directives whose
  fields are all live. Other directives behave as in shadow.
- **`disabled`:** section 6.6.

### 6.2 Enabling

Turning the toggle on starts the feature in `shadow` with a provisional owner
program.

### 6.3 Going live

The first time `live` is chosen, the options flow shows a snapshot of the
device (mode, setpoint, the reservation switch and the owner's entries) for
confirmation as the owner's program. It shows how the owner's entries will be
preserved when the feature turns the reservation switch on (section 5.1).

### 6.4 Unload and restart

**Stopping Home Assistant or reloading the integration writes nothing.** The
programmed entries keep running on the device.

### 6.5 Start-up

1. Read the device's list.
2. Compare the stored intent with the entity's document by `issued_at`, and
   take the newer.
3. If that is the stored intent, keep its entries: entries that have not
   fired stay, and fired entries are removed in the next write (section 5.3).
4. If it is a newer document, replace the stored intent directive by
   directive (section 5.6).
5. A window whose start passed during the outage, and whose start entry fired,
   is `in_force`. It is not written again.

Start-up never withdraws a programmed window because time has passed.

### 6.6 Disabling

Switching to `disabled`, by the Disable button or the options, is a
**one-off, unconditional** clean-up:

1. Remove every entry the feature owns.
2. Restore the owner's reservation switch and the owner entries' own enable
   flags (section 5.1).
3. If a window is in force, write the owner state now (section 5.5).
4. Read back and report on the last direct write entity.

After that the feature writes nothing until the mode is changed in the
options flow. Turning the toggle off does the same, then removes the entities
and the stored data.

---

## 7. Configuration (options flow)

| Option | Default |
|---|---|
| External control enabled | off |
| Mode | `shadow` |
| Live switch per field (`setpoint`, `mode`) | both off |
| Intent entity | none (required to enable) |
| Setpoint min / max | The device's `dhw_temperature_min` / `max` |
| Allowed modes | `energy_saver` |
| Assisted mode | `energy_saver` |
| Entry limit | 7, until section 8 measures it |
| Owner's program | Declared from a snapshot the first time `live` is chosen (section 6.3) |

Changing an option updates the capability entity, and so its version.

---

## 8. Device tests before any live write

Each needs the owner present. Each result updates a capability fact or a rule
in this document.

1. **Clock.** An entry written three minutes ahead fires at that minute in
   Home Assistant's time zone.
2. **Entry limit.** The largest list the device confirms.
3. **Writing the list.** Whether writing the list, with no entry firing,
   starts a recovery.
4. **Same-mode entries.** Whether an entry whose mode equals the current mode
   still counts as a mode write and starts a recovery.
5. **Entry mode in a TOU window.** Whether the mode is held until the window
   ends, applied at the end, or discarded.
6. **Power-off and Vacation.** Whether entries fire, and whether an entry's
   mode turns the heater back on from power-off.
7. **Anti-Legionella.** Whether an entry firing mid-cycle interrupts it.
8. **Per-entry enable flag.** Whether an entry with its own flag off is
   skipped while the reservation switch is on.
9. **Offline.** Whether an entry fires while the device is disconnected from
   the cloud, and whether the list survives a power cut with the clock
   resynchronised.

---

## 9. Out of scope

- **Scheduling, forecasting, pricing or deciding anything.** The feature
  programs an intent; it never makes one.
- **Surplus logic.** A scheduler that sees surplus publishes a setpoint window
  starting now. The feature programs its end and writes its start directly.
- **Cycle policy:** minimum run times, not stopping a running compressor, not
  reversing within a cycle. The scheduler chooses window ends using the facts
  in section 4.1. The feature cannot enforce such rules anyway, because the
  device fires an end entry whatever the compressor is doing.
- **The TOU switch and schedule** (section 5.7).
- **MQTT, or any transport.** The intent entity is the interface.
- **Estimating tank physics.** Intents come in temperatures and times.

---

## 10. Changes from the first draft

| First draft | This revision | Why |
|---|---|---|
| Direct writes as a second main channel: mode windows written directly at start and end, hold-off written directly at start, surplus raises and the TOU lever written directly | Every change is an entry pair. Direct writes only for a window already begun, a withdrawn window in force, and disabling, each after its end entry is confirmed | The heater must keep running the plan when the service is unavailable |
| `valid_until`; a stale intent is withdrawn, and start-up deletes a stale intent's entries | Removed. Programmed entries run out on their own; only a new intent or disabling removes them | A silent scheduler or an outage must not end the schedule |
| A daily-revert entry at 03:00, and re-applying the intent afterwards | Removed. Every state has its own end entry | It cut every window spanning 03:00 short when Home Assistant was down, and was a daily write that could start a recovery |
| Directive types `charge`, `hold_off`, `mode` and `surplus_grant`, with decisions inside the feature: a tank-relative hold setpoint, completion on the compressor stopping, surplus timers, request cycles | One directive shape: a window with a setpoint, a mode, or both | The feature actuates; the scheduler decides |
| Minimum run before stop, no reversal within a cycle, and restores waiting for the compressor | Removed; the device facts are declared instead | A device-side end entry fires regardless. The draft also contradicted itself: a charge's end was a hard limit, yet its restore waited |
| An override blocked its field until 03:00, including the reservation list | A change inside a window is reported only. A change outside every window becomes the owner's state. Owner entries are always preserved | With the list as the interface, a day-long pause would stop the controller |
| A constant baseline | The owner's program: the owner's entries at each minute, plus a default | Owner entries change the state during the week |
| Enabling reservations could re-activate disabled owner entries | Owner entries keep their behaviour through their own enable flags | The owner's program is never changed |
| The entry's mode unstated | Every entry carries the full wanted state | A device entry always sets both |
| No limit on how far ahead | A 144-hour horizon | A weekly entry fires at its next weekday occurrence |
| Precedence deleted pending entries | Entries stay; the device suspends reservations in Vacation itself | Deleting entries is a Home Assistant dependency |
| `applied` meant a write read back | `programmed`, `in_force` and `ended`, with read-back at each boundary | An entry's effect is only observable when it fires |
| The TOU lever | Removed | Nothing on the device could undo it |

---

## 11. Delivery

1. **This specification, the JSON Schema and example documents** in `docs/`.
2. **Skeleton:** the options toggle and the disabled-path regression test;
   intake, validation and the stored intent; the capability entity; `shadow`
   as the default mode; the heartbeat; unload without writes. This exists on
   the feature branch and is adapted to this revision.
3. **Shadow programming:** the owner's program; entries, horizon and budget;
   reading the list and reconciling; the program, in-sync and wanted
   entities; people's changes; simulated direct writes. This replaces the
   first draft's shadow engine.
4. **The device tests** in section 8.
5. **Live list writes,** the `setpoint` field first and then `mode`, followed
   by the three direct writes.
6. **Protocol `1`** after a staged live cut-over.

Related: #157 (`water_heater` service reports success in two failure cases).
Device behaviour this relies on is documented in `nwp500-python`
(`docs/explanation/what-starts-a-recovery.rst`, eman/nwp500-python#147;
`docs/explanation/tou-recovery-cap.rst`; `docs/how-to/schedule-operation.rst`).

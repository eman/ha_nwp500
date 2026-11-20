# External control protocol 1: reference

This page is the normative reference for external control, protocol `1`:
what a scheduler publishes, how the feature checks and programs it, and what
the feature's entities report. The
[scheduler guide](external-control-scheduler-guide.md) shows how to use it.
[`external-control.md`](external-control.md) covers setting the feature up.

The JSON Schema
[`external-control-protocol-1.schema.json`](external-control-protocol-1.schema.json)
is the machine-readable form of the plan and of every entity's attributes.
Sample plans, acknowledgements and a list write are in
[`examples/`](examples/).
The [design record](external-control-design.md) holds the reasoning and
history; where it differs from this page, this page and the schema win.

Names: a **plan** is the protocol's *intent* (its id is `intent_id`), and a
**plan step** is a *segment*. Raw states, attribute names and values are the
protocol's, whatever Home Assistant displays.

## 1. Versions

| Version | Adds |
|---|---|
| `1` (`1.0`) | The plan document, the entities and the compatibility promises below. Its format is that of the revised protocol `0` |
| `1.1` | The segment key `reassert` |
| `1.2` | Nothing beyond 1.1 today. It added timing rules for surplus grants, which were removed from the protocol |
| `1.3` | Device commands: the plan's `commands`, and the acknowledgement's `commands` |
| `1.4` | The capability declaration's `modes`, `setpoint` and `commands`; the rejection `setpoint_out_of_range`; range checks on a command's whole-number keys (`invalid_command`) |

- The feature accepts a document whose `protocol` is `"1"`, any `"1.x"`,
  `"0"` or any `"0.x"`. `"0"` means the same as `"1"`.
- The capability declaration's `protocols` lists the majors accepted,
  `["1", "0"]`. `protocol_versions` gives the newest minor implemented of
  each, in the same order: `["1.4", "0"]`. Every earlier minor is implemented
  too. A declaration without `protocol_versions` implements `1.0`.
- Compare minor versions as numbers: `1.10` comes after `1.9`.
- A scheduler relies on a minor version's keys only when `protocol_versions`
  lists that minor or a later one. A feature that knows only an earlier minor
  keeps newer keys as opaque ones.

Within major version `1`:

1. A minor version only adds optional keys. A key the feature does not know
   is opaque and echoed, never an error.
2. Entities, their unique ids, states and attribute names keep their
   meaning. New entities, attributes, statuses, reasons, warnings and
   last-write reasons may be added. **Treat a value you do not know as
   opaque, not as an error.**
3. The capability declaration's keys keep their meaning; new keys may be
   added. Its `version` changes whenever it does.
4. A breaking change needs protocol `2`. The feature then lists both majors
   in `protocols`.

## 2. Input: the plan entity

The scheduler publishes each plan on one Home Assistant entity of its own,
chosen as the **Plan entity** in the integration's options.

| Part | Rule |
|---|---|
| State | MUST change on every new plan. Use the `intent_id` |
| Attributes | The plan document: its top-level keys are the entity's attributes |
| Presentation attributes | `friendly_name`, `icon`, `entity_picture`, `supported_features`, `device_class`, `state_class`, `unit_of_measurement`, `attribution`, `restored` and `assumed_state` are removed before the document is read. They are never part of a plan and never echoed |
| `unknown` or `unavailable` | Ignored: neither a plan nor a stop. The plan in force stays in force |
| Removed from Home Assistant | The plan in force stays in force. Repairs reports the entity missing until it is back |

The feature reads every `state_changed` event of the entity, an
attribute-only change included. The feature stores the last accepted plan.
At start-up, see [section 6.10](#610-start-up).

Every heater of the config entry follows the same plan entity. Each heater
checks, programs and reports the plan on its own entities (section 7).

## 3. The plan document

### 3.1 Top level

| Key | Type | Required | Rule |
|---|---|---|---|
| `protocol` | string | yes | `"1"`, `"1.x"`, `"0"` or `"0.x"` (section 1) |
| `intent_id` | string, 1 to 64 characters | yes | Unique per plan. Use it as the plan entity's state |
| `issued_at` | timestamp (section 3.5) | yes | When the scheduler made the plan. Compared at full precision |
| `segments` | list of segments | yes | The timeline, in strictly increasing order of `start`. **An empty list stops the plan**: every entry of the feature's is withdrawn and the heater keeps the state it is in |
| `commands` | list of commands | no | Protocol 1.3. Device commands, each applied once when the plan is adopted |
| any other key | any | no | Opaque. Echoed on the plan ID entity (section 7) |

There is no validity period. The last segment holds until a new plan
arrives, so make it a state you are willing to hold indefinitely.

### 3.2 Segments

A segment sets the heater's state from its `start` until the next segment's
`start`. The last segment has no end. Until the plan's first segment starts,
whatever was in force continues: the previous plan's segment, or the
heater's own state.

| Key | Type | Required | Rule |
|---|---|---|---|
| `id` | string, 1 to 64 characters | yes | Unique in the document, command ids included |
| `start` | timestamp | yes | Truncated to the minute |
| `setpoint_f` or `setpoint_c` | number | exactly one | Converted once to half degrees Celsius (section 3.6). A key named `setpoint` is not allowed |
| `mode` | mode name (section 3.3) | on the first segment | A later segment without one keeps the previous segment's mode |
| `reassert` | boolean | no, default `false` | Protocol 1.1. `true` gives the segment its own entry even when its state repeats the segment before it, so a person's change is ended at its start |
| any other key | any | no | Opaque. Echoed on the segment's acknowledgement |

### 3.3 Mode names

| Name | Heater mode |
|---|---|
| `heat_pump` | Heat Pump |
| `energy_saver` | Energy Saver (hybrid) |
| `high_demand` | High Demand |
| `electric` | Electric |
| `power_off` | Powered off |

These are every mode a reservation entry can set, and the declaration's
`modes`. Each is applied as given; what it does is the scheduler's to know.

- An entry has no Vacation, so `vacation` is not a segment mode. Use the
  `vacation` command. The heater skips entries during Vacation, so a later
  entry does not end it.
- A `power_off` entry carries the reservation mode 5 (6 before
  nwp500-python 9.5.0). On the heater tested it powered the heater off at
  its minute; the heater kept its own setpoint, not the entry's, and the
  next segment's entry turned it on again. The mode command with the value
  6 switched the unit tested to Energy Saver (#160).

### 3.4 Commands (protocol 1.3)

Settings a reservation entry cannot set. Mode and setpoint are set by
segments only.

| Key | Type | Required | Rule |
|---|---|---|---|
| `id` | string, 1 to 64 characters | yes | Unique in the document, segment ids included |
| `command` | string | yes | One of the table below |
| the command's keys | as below | as below | |
| any other key | any | no | Opaque. Echoed on the command's acknowledgement, except a key named `id`, `command`, `status`, `reason`, `warnings` or `detail` |

| `command` | Keys | Library call | Read back from |
|---|---|---|---|
| `vacation` | `days`: whole number, 1 to 99 | `set_vacation_days` | The mode reported as Vacation, and the vacation days |
| `power` | `on`: boolean | `set_power` | The mode: `power_off` or not |
| `anti_legionella` | `enabled`: boolean; `period_days`: whole number, 1 to 30, given only with `enabled: true` and then required | `enable_anti_legionella` / `disable_anti_legionella` | Anti-Legionella on or off, and its period when on |
| `tou` | `enabled`: boolean | `set_tou_enabled` | The TOU switch |
| `demand_response` | `enabled`: boolean | `enable_demand_response` / `disable_demand_response` | Nothing the heater reports: `applied` once sent |

A whole number may be written as `5` or `5.0`; `true` and `false` are not
numbers. A boolean must be `true` or `false`.

### 3.5 Times

- A timestamp is ISO 8601 with a UTC offset, such as
  `2026-10-04T05:00:00-07:00` or `...Z`. A timestamp without an offset is
  rejected (`invalid_document`).
- A segment's `start` is truncated to the minute: entries fire at the start
  of their minute. Order is checked after truncation.
- `issued_at` is kept and compared as given, not truncated.
- Any offset may be used. Entries are placed at the local minute in Home
  Assistant's time zone, which is the zone the heater fires them in.

### 3.6 Setpoints

A segment's setpoint is converted once, on parsing, to the heater's unit:
whole half degrees Celsius (`raw`). The library's `HalfCelsius` does it:

- `setpoint_c`: `raw = round(setpoint_c × 2)`.
- `setpoint_f`: `raw = round((setpoint_f − 32) × 5 / 9 × 2)`, in floating
  point.
- `round` is Python's: to the nearest whole number, and a value exactly
  halfway goes to the even one. `60.25 °C` gives 120 (60.0 °C); `60.75 °C`
  gives 122 (61.0 °C).

Entities report a setpoint back as `setpoint_c = raw / 2` and
`setpoint_f = raw / 2 × 9 / 5 + 32`, each rounded to one decimal. So
`135 °F` is raw 114, 57.0 °C, and reads back as 134.6 °F; `150 °F` is raw
131, 65.5 °C, and reads back as 149.9 °F.

Everything after parsing uses `raw`: the range check, merging (section 6.2)
and every entry. Two setpoints with the same `raw`, such as `140 °F` and
`140.1 °F`, are the same state.

A setpoint must be a finite number no larger than 1000 in magnitude, or the
document is `invalid_document`.

**Range (protocol 1.4).** Once the heater's features have been read, its
own setpoint range is the declaration's `setpoint`, bounds included. A
segment whose `raw` is outside it rejects the whole document
(`setpoint_out_of_range`). Each bound in `setpoint` converts back to the
heater's own limit, so a setpoint equal to a bound is accepted. While
`setpoint` is `null` plans are not range-checked; a setpoint the library
then refuses makes the list write fail like any unconfirmed write
(`write_not_confirmed`, section 6.7).

### 3.7 Plan identity

A plan is its `intent_id` and its `issued_at` together.

| The document, against the plan in force | Result |
|---|---|
| Same `intent_id`, same `issued_at` | The plan in force received again. It is still checked (section 4.1); if it passes, nothing changes: what else it says is not adopted, and its commands are not sent again |
| `issued_at` earlier | Rejected, `superseded` |
| `issued_at` the same or later, otherwise | Accepted: a new plan, which replaces the plan in force (section 6.6) |

So a changed plan needs a new `issued_at`, and should have a new
`intent_id`. A document waiting for a write in progress is checked again
after it: a plan adopted meanwhile with a later `issued_at` makes it
`superseded` too.

### 3.8 Opaque keys

| Where | Echoed on | Keys that win over an opaque key of the same name |
|---|---|---|
| Top level | The plan ID entity's attributes | `issued_at`, `received_at`, `segment_count` |
| Segment | Its item in the acknowledgement's `segments` | `id`, `status`, `reason`, `warnings`, `fires_at`, `in_force` |
| Command | Its item in the acknowledgement's `commands` | `id`, `command`, `status`, `reason`, `warnings`, `detail`, and the command's own keys: none of these is echoed |

An unsupported command has no keys of its own, so its other keys are all
echoed.

## 4. Validation

### 4.1 Document rejections

The whole document is rejected, and only the first problem found is
reported. The document is read in order: the structural checks
(`invalid_document`, `unsupported_protocol`, `unordered_segments`,
`duplicate_id`) come as each part is read; then each segment, in order, is
checked for `mode_not_allowed` and then `setpoint_out_of_range`; then
`superseded`.

| Reason | When |
|---|---|
| `invalid_document` | A required key is missing (`protocol`, `intent_id`, `issued_at`, `segments`). `protocol` is not a string of the form `N` or `N.M`. `intent_id` is not a string of 1 to 64 characters. A timestamp is not a string, not ISO 8601, has no UTC offset, or is within 8 days of the limits of year 1 or 9999. `segments` is not a list, or an item is not an object. A segment's `id` is missing or not a string of 1 to 64 characters, its `start` is missing, it has not exactly one of `setpoint_f` and `setpoint_c` (or has `setpoint`), its setpoint is not a number, not finite or larger than 1000 in magnitude, its `mode` is not a string, or its `reassert` is not a boolean. The first segment has no `mode`. `commands` is not a list, an item is not an object, or a command's `id` is missing or not a string of 1 to 64 characters |
| `unsupported_protocol` | `protocol`'s major version is not `1` or `0` |
| `unordered_segments` | After truncation to the minute, a segment does not start after the one before it |
| `duplicate_id` | Two items share an id: two segments, two commands, or a segment and a command |
| `mode_not_allowed` | A segment's mode is not a mode name (section 3.3) |
| `setpoint_out_of_range` | Protocol 1.4. The heater's range is known and a segment's setpoint is outside it (section 3.6) |
| `superseded` | `issued_at` is earlier than the plan in force's (section 3.7) |

A segment is part of a timeline, so one bad segment rejects the plan rather
than leave the segment before it in force over its time.

A rejected document leaves the plan in force unchanged, its entries
included. The acknowledgement reports the rejection (section 8.1).

### 4.2 Command rejections

A malformed or unknown command is rejected on its own, and the plan goes
ahead. The command is never sent; its acknowledgement says why.

| Reason | When |
|---|---|
| `unsupported_command` | `command` is a string the feature does not know |
| `invalid_command` | `command` is not a string or is missing; a key of the command is missing or of the wrong type; `days` or `period_days` is out of range (protocol 1.4); `period_days` is given without `enabled: true` |

Only the list's shape and the ids can reject the document (section 4.1).

## 5. Capability declaration

The capabilities entity's state is the declaration's `version`; its
attributes are the declaration. Schema: `capabilities_attributes`.

| Attribute | Value |
|---|---|
| `protocols` | Majors accepted: `["1", "0"]` |
| `protocol_versions` | Newest minor of each, same order: `["1.4", "0"]` |
| `feature_version` | The integration's version. Informational |
| `mode` | What runs: `shadow`, `live` or `disabled`. An option stored as `live` that writes nothing runs, and is declared, as `shadow` |
| `setpoint_resolution_c` | `0.5`: setpoints are whole half degrees Celsius |
| `horizon_h` | `144`: an entry is programmed only for a segment that starts less than this many hours ahead |
| `near_term_lead_min` | `2`: a change needed now is an entry for the first minute at least this many minutes ahead |
| `entry_limit` | The most entries the heater's list may hold, every entry included. Option, default 16, 1 to 32 |
| `entry_reserve` | Entries kept free for near-term entries. Option, default 2, less than `entry_limit` |
| `modes` | Protocol 1.4. The mode names a segment may use (section 3.3) |
| `setpoint` | Protocol 1.4. `{"min_f", "max_f", "min_c", "max_c"}`, the heater's own range, inclusive, each rounded to one decimal; `null` until the heater's features are read. Plans are not range-checked while it is `null` |
| `commands` | Protocol 1.4. Each command the feature applies: `{name: {key: {"type": "integer" or "boolean", "min"?, "max"?}}}`. `min` and `max` are inclusive. When a key is required is in section 3.4 |
| `entries_available` | `entry_limit` minus every entry in the program (other entries and the feature's own) minus `entry_reserve`, at least 0. `null` until the heater's list is read. It changes as entries fire, so it is left out of `version` |
| `version` | The first 8 hex digits of a SHA-256 of the declaration without `entries_available`. Changes whenever the declaration does: a mode or option change, the heater's range becoming known, an upgrade |

Example, with the heater's range read and nine entries free:

```json
{
  "protocols": ["1", "0"],
  "protocol_versions": ["1.4", "0"],
  "feature_version": "0.21.1",
  "mode": "live",
  "setpoint_resolution_c": 0.5,
  "horizon_h": 144,
  "near_term_lead_min": 2,
  "entry_limit": 16,
  "entry_reserve": 2,
  "modes": ["heat_pump", "energy_saver", "high_demand", "electric", "power_off"],
  "setpoint": {"min_f": 104.9, "max_f": 149.9, "min_c": 40.5, "max_c": 65.5},
  "commands": {
    "vacation": {"days": {"type": "integer", "min": 1, "max": 99}},
    "power": {"on": {"type": "boolean"}},
    "anti_legionella": {
      "enabled": {"type": "boolean"},
      "period_days": {"type": "integer", "min": 1, "max": 30}
    },
    "tou": {"enabled": {"type": "boolean"}},
    "demand_response": {"enabled": {"type": "boolean"}}
  },
  "entries_available": 9,
  "version": "3e94fc27"
}
```

## 6. How a plan is carried out

### 6.1 Modes of the feature

| `mode` | Writes | Segment statuses | Commands |
|---|---|---|---|
| `shadow` (Preview) | Nothing. Every write is simulated: the entities show the list as if written | `shadow`, `scheduled`, `merged`, `ended` | Evaluated, status `shadow`; applied on going live |
| `live` | The heater's reservation list, and the plan's commands | Section 8.2 | Section 8.3 |
| `disabled` (Stopped) | Nothing. No plan is adopted; the entities stay | None: the acknowledgement is `none` | None |

Going live on a heater that holds nothing of the feature's discards what
shadow simulated and programs the plan in force afresh: its segment in force
gets a near-term entry if the heater reports another state, and its
commands with status `shadow` are sent. Leaving `live` writes nothing. What
the plans put on the heater stays there, firing every week, until a plan or
a person changes it.

### 6.2 From segments to entries

- **One entry per segment**, at its start, carrying its mode and setpoint.
  A device entry always sets both.
- **Merged.** A segment with the same mode and `raw` setpoint as the
  segment before it gets no entry. Its status is `merged`. A segment with
  `reassert: true` is never merged.
- **Near-term entries.** A segment already begun gets an entry for the first
  minute that starts at least `near_term_lead_min` (2) minutes ahead. When a
  plan is adopted, its segment in force gets one only if the heater reports
  a different mode or setpoint. None is written if a segment changing the
  state starts by then.
- **Too close.** A segment starting less than `near_term_lead_min` ahead is
  not programmed. It is `scheduled` with no reason, and gets a near-term
  entry once it begins.
- **Horizon.** A segment starting `horizon_h` (144) hours or more ahead is
  `scheduled`, `beyond_horizon`. It is programmed once it comes within the
  horizon. A weekly entry cannot name a date: one a week or more away would
  fire a week early.
- **Budget.** Plan entries fit in
  `min(entry_limit − others − entry_reserve, entry_limit − others − near-term entries)`,
  at least 0, where `others` is every entry on the heater that is not the
  feature's. Segments are taken in time order. The rest are `scheduled`,
  `entry_budget`, and are programmed as earlier entries fire and are
  removed.
- **One enabled entry per minute.** A plan entry that would share a weekday
  and minute with another enabled entry, the plan's own included, moves a
  minute later until it is free, with the warning `moved_1_min`. It fires
  after its segment begins, and stays until it fires, unless the next
  segment changing the state begins first. It may share a minute with an
  entry switched off by its own flag.
- **Other entries** (the owner's, anyone's) stay on the heater exactly as
  read, switched on or off, fire as they are set, and count against
  `entry_limit`.
- **Fired entries** are removed by a later write. A write that only removes
  fired entries (`cleanup`) waits for their read-back, or 15 minutes past it
  without a fresh status; any other write removes them at once, and they are
  then not read back. In shadow they go at their minute. While the feature
  is unavailable they stay, so a week later the heater repeats them.

### 6.3 The reservation switch

The first live write of the feature's own entries turns the heater's
reservation switch on (last write `reason`: `takeover`, when it adds
nothing). A plan with no entries of its own leaves the switch as it is.
Once the feature holds the list it keeps the switch as read: a person who
turned it off keeps it off, and no entry fires.

### 6.4 Writing the list (live)

- The feature reads the heater's list before every write, keeps every entry
  that is not its own, and writes the list whole.
- A write counts only once the heater reads back the list sent.
- An unconfirmed write is retried once after 60 seconds. If the retry is not
  confirmed either, the segments its new entries served are `failed`,
  `write_not_confirmed`, and writing pauses for 15 minutes before it tries
  again. A confirmed write later clears the failure, except for a segment
  that has ended by then.
- Changes that arrive during a write go into the next one. A plan adopted
  while writing is paused is written when the pause ends.
- A write whose confirmation was lost may still have landed. The next read
  settles it: entries of the feature's found on the heater are its own, not
  a person's.
- A near-term entry whose minute passes before it is written, or during a
  pause, is moved to the next minute it can make. It is dropped once a later
  segment is in force.

### 6.5 Read-back (live)

After an entry's minute plus the poll interval and one minute, the heater's
reported mode and setpoint are compared with the entry's, once, against a
status reported after the entry's minute. If none comes within 15 minutes,
the entry is not checked. A difference makes the segment `failed`,
`not_applied_on_device`. A `power_off` entry is checked by its mode alone.
Any reported state counts: a mode held by a TOU window, Vacation or a
person's change all differ.

### 6.6 Replacing a plan

An accepted new plan applies from its receipt, in one write:

- Entries already on the heater that the new plan wants, for a segment of
  the same id, state and minute, are kept. The rest of the old plan's
  unfired entries are removed.
- The segment in force gets a near-term entry only if the heater reports a
  different mode or setpoint, a person's change included.
- The new plan is programmed as it stands. Entries people removed from the
  old plan are written again if the new plan wants them, `removed` statuses
  start afresh, and the override entity's reports end.
- An empty `segments` list withdraws every entry of the feature's. The
  heater keeps its state. The acknowledgement is `pending` until they are
  gone.

### 6.7 Commands

- Adopting a plan applies its commands once each, in list order, in the
  pass that writes its list and after that write, whether or not the write
  was confirmed.
- Every command of every adopted plan is sent. Nothing is kept of a plan's
  commands once another plan is adopted. A plan in force received again, or
  restored after a restart, does not send them again.
- After sending, the feature asks for a status and compares the setting the
  command set (section 3.4). The heater has the poll interval plus one
  minute to report it.
- A status reports the application only. A person changing the setting
  afterwards is not undone, and the command stays `applied`.
- A later command that changes what an earlier one set (Vacation, then
  power off) can leave the earlier one unreported. Send the second in a
  later plan if it must wait for the first.

### 6.8 People's changes

- **A deleted entry.** If a person deletes the entry that puts a segment in
  force (its plan entry, or the near-term entry of a segment already begun)
  before its minute is over, the segment is `removed` and the override
  entity reports it. Nothing is written for that segment, or for the merged
  segments after it, while the plan is in force, across restarts. A new plan
  is the scheduler's answer (section 6.6).
- A deleted entry whose minute is over changes nothing. Deleting an entry
  the plan wants removed (an earlier plan's) is not reported.
- **An added entry** is not the feature's: it is kept, counts against the
  budget, and fires as set. It appears in the program's `entries` as
  `foreign` from the first read of the list that holds it.
- **The heater's setpoint and mode**, changed by a person, are not reported
  by the feature. Read them from the heater's own entities. Read-back
  reports a difference after an entry (section 6.5).
- **The reservation switch** is the heater's to report: the Reservation
  Schedule sensor's `enabled`.

The heater reports its list only when asked: at start-up, every 40 status
polls, before every live write, and when the `nwp500.request_reservations`
action is called.

### 6.9 When something is unavailable

| Unavailable | What happens |
|---|---|
| The scheduler | The feature keeps programming the stored plan's remaining segments as the horizon and room allow. The last segment holds until a new plan |
| The plan entity | As above: the plan in force stays. Clearing the entity withdraws nothing |
| Home Assistant, or the feature | The heater runs the entries already programmed, up to `plan_written_up_to`, then holds the last state. Fired entries stay, and repeat a week later |
| The heater's connection | List writes and commands fail and are reported (sections 6.4, 6.7) |

### 6.10 Start-up

A start (Home Assistant's, or a reload of the integration) sets the
heartbeat's `started_at`, then:

1. The stored plan is restored. If the plan entity's document is not the
   same plan (section 3.7), it is then received as any document: a newer
   one replaces the stored plan, and an older one is rejected `superseded`.
2. A stored plan from another plan entity (the option changed) is dropped,
   and so is one that no longer passes the document checks (section 4.1).
   The entity's document is then taken whatever its `issued_at`. With no
   usable document there is no plan in force, and the stored plan's entries
   are withdrawn.
3. A restored plan keeps its entries, its `removed` segments and its
   commands' statuses. Its segment in force is not written again, and its
   commands are not sent again.
4. Until the heater's list has been read, the program is unknown and
   `entries_available` is `null`.

Unload and restart write nothing to the heater.

## 7. Entities

One set per heater, owned by its device. All are available whenever the
feature is loaded, including while the heater is unreachable.

- **Unique id:** `<mac>_control_<key>`, where `<mac>` is the heater's MAC
  address as the integration holds it. Find entities by unique id: the
  entity id can be changed by the user.
- **Default entity id:** `<domain>.<device>_<id>`, where `<device>` is the
  device's name as a slug (`navien_nwp500` for a device without a name of
  its own) and `<id>` is fixed by the key, whatever the display name.
- Display names, translated state labels and entity categories are not part
  of the protocol.

| Key | Platform | Default id after `<device>_` | Schema `$defs` | Category |
|---|---|---|---|---|
| `capabilities` | sensor | `external_control_capabilities` | `capabilities_attributes` | diagnostic |
| `intent` | sensor | `plan_id` | `intent_attributes` | |
| `ack` | sensor | `plan_status` | `ack_attributes`, `segment_ack`, `command_ack` | |
| `program_hash` | sensor | `plan_fingerprint` | `program_hash_attributes`, `program_item`, `device_entry` | diagnostic |
| `in_sync` | binary_sensor | `heater_matches_plan` | `in_sync_attributes` | |
| `programmed_until` | sensor | `plan_written_up_to` | `programmed_until_attributes` | |
| `next_entry` | sensor | `plan_next_change` | `next_entry_attributes` | |
| `wanted_mode` | sensor | `plan_mode_now` | none: no attributes | |
| `wanted_setpoint` | sensor | `plan_temperature_now` | `wanted_setpoint_attributes` | |
| `last_write` | sensor | `external_control_last_write` | `last_write_attributes`, `entry_item` | diagnostic |
| `override` | binary_sensor | `plan_entry_removed` | `override_attributes`, `override_report`, `entry_item` | |
| `heartbeat` | sensor | `external_control_heartbeat` | `heartbeat_attributes` | diagnostic |
| `disable` | button | `stop_external_control` | none | |

### 7.1 States and attributes

| Key | State | Attributes | When empty |
|---|---|---|---|
| `capabilities` | The declaration's `version` | The declaration (section 5) | Always set. `setpoint` and `entries_available` are `null` until read |
| `intent` | The plan in force's `intent_id`, or `none` | `issued_at`, `received_at`, `segment_count`, and the plan's opaque top-level keys | State `none`; `issued_at` and `received_at` `null`, nothing else |
| `ack` | `none`, `rejected`, `shadow`, `pending`, `partly_programmed` or `programmed` (section 8.1) | `intent_id`, `reason`, `detail`, `segments` (section 8.2), `commands` (section 8.3), `rejected` | State `none`; `intent_id`, `reason`, `detail` and `rejected` `null`; `segments` and `commands` empty |
| `program_hash` | The `schedule_hash` of the list the feature wants on the heater, comparable with the Reservation Schedule sensor's | `entry_count`; `entries`: every entry of that list, each with the device's keys and `owner` (`foreign`, or the kind of one of the feature's own, which also carries the entry item keys, with `mode` the device's mode id and `mode_name` the name) | Unknown until the heater's list is read since the start, with `entry_count` and `entries` `null`. With no plan, the heater's own entries |
| `in_sync` | On when the heater's list hashes the same as the program, reservation switch included | `device_hash`, `read_at` (when that list was first seen) | Unknown until the list is read, with both `null` |
| `programmed_until` | Timestamp: the start of the first segment still waiting to be written (`scheduled`), or of the last segment once none waits. Merged and removed segments do not wait | `complete` (no segment waits), `scheduled` (how many wait) | Unknown with no plan or an empty one; `complete` `null` with no plan; `scheduled` 0 |
| `next_entry` | Timestamp: when the next entry of the feature's fires | `mode`, `setpoint_f`, `setpoint_c`, `kind`, `serves` | Unknown; `mode`, `setpoint_f`, `setpoint_c` `null`, no `kind` or `serves` |
| `wanted_mode` | The mode of the plan's segment in force: the last segment that has started, a merged one counting as the one it merged into | None | Unknown before the first segment starts, or with no plan |
| `wanted_setpoint` | That segment's setpoint, in Home Assistant's temperature unit, to one decimal | `segment`: the id of the segment in force, as the acknowledgement's `in_force`; `null` when none is, or when a person removed its entry | Unknown; `segment` `null` |
| `last_write` | Timestamp of the last list write, sent or simulated | `reason` (section 8.4); `added` and `removed` (entry items); `added_count`, `removed_count`, `truncated`, `confirmed`, `simulated` | Unknown before the first write; the only attribute is `reason: null` |
| `override` | On while a person's removal of a plan entry is reported | The latest report's `field`, `value`, `detected_at`, `segment`; `reports`; `report_count`; `truncated` | Off; `field`, `value`, `detected_at`, `segment` `null`; `reports` empty; `report_count` 0; `truncated` false |
| `heartbeat` | Timestamp, updated at least every 15 minutes while the feature runs | `started_at`: when the feature last started | Always set |
| `disable` | Button | None | Pressing it sets the feature's mode to `disabled` |

The plan mode and temperature entities follow the plan's timeline: they
show the segment in force's state even when a person removed its entry,
and then `segment` is `null` and the segment's `in_force` is `false`.

**Entry items** (`entry_item`), in the last write's `added` and `removed`
and in an override report's `value`:

| Key | Meaning |
|---|---|
| `kind` | `plan` or `near_term` |
| `owner` | The kind again |
| `serves` | The id of the segment it serves; the same string as `plan_next_change`'s `serves` |
| `fires_at` | The local minute it is written for, after any `moved_1_min` shift. An entry left on the heater fires again a week later |
| `mode` | The mode name |
| `setpoint_f`, `setpoint_c` | The setpoint, one decimal |
| `setpoint_raw` | The setpoint in half degrees Celsius |
| `week`, `hour`, `min` | The device slot: the weekday bit of `fires_at`'s local date (Monday 64, Tuesday 32, Wednesday 16, Thursday 8, Friday 4, Saturday 2, Sunday 128), hour and minute |

**Last write.** `confirmed` is for the whole write: `true` once the heater
read back the list sent; `false` from a first unconfirmed attempt, or for a
live write that could not be sent; `null` for a simulated write.
`simulated` is `true` in shadow.

**Override reports.** `field` is `removed`; `value` is the removed entry as
an entry item; `segment` is its `serves`; `detected_at` is when it was
found. One report per segment and kind of entry: a newer one replaces it.
Reports are kept across restarts and end when a new plan is adopted. To
see every report, record the entity's state changes rather than polling.

**Size.** The recorder keeps no attributes of a state over 16 KiB. The
last write sets `removed`, then `added`, to `null` when its attributes
would pass 15 KiB, and sets `truncated`; the counts stay. The override
entity drops the oldest items of `reports` and sets `truncated`;
`report_count` counts them all, and the latest report is always in the top
level.

**Republishing.** Every control entity writes its state again, unchanged,
once Home Assistant has started and on every MQTT connection, so
`mqtt_statestream` and a broker without retained copies have them all.
Only the state's timestamps move.

## 8. Statuses

### 8.1 Acknowledgement

| State | When |
|---|---|
| `none` | No plan in force, and no document rejected since the start. Always so while `disabled` |
| `rejected` | No plan in force, and the most recent document was rejected. `intent_id`, `reason` and `detail` are that document's; `segments` is empty |
| `shadow` | A plan is in force and the mode is `shadow` |
| `pending` | Live: a segment is `pending`, or the heater still holds unfired entries of the feature's that the plan wants removed. An empty plan is `pending` until they are gone |
| `partly_programmed` | Live: not `pending`, and a segment is `removed` or `failed` |
| `programmed` | Live: not `pending`, and no segment is `removed` or `failed`. Segments may still be `scheduled` |

`reason` and `detail` are set only in the `rejected` state. `rejected` holds
the most recent document rejected whole (`intent_id`, `reason`, `detail`),
or `null`. A rejection does not change the plan in force, its state or its
`segments`. `rejected` is cleared when a document received after the
rejected one is accepted.

So a document was **rejected** when `rejected.intent_id` is its id, and
**accepted** when the acknowledgement's `intent_id` is its id and
`rejected` does not name it.

### 8.2 Segments

Each item of `segments`, in the plan's order: `id`, `status`, `reason`,
`warnings` (a list), `fires_at`, `in_force`, and the segment's opaque keys.

- `fires_at`: the minute its entry fires, or will fire once programmed; for
  a segment already begun, its near-term entry's; `null` if it has none.
- `in_force`: whether it is the plan's segment in force now. A merged
  segment counts as the one it merged into; a removed one never is.

| Status | Meaning | Mode |
|---|---|---|
| `shadow` | Would be written, or is in force | shadow |
| `scheduled` | Not programmed yet. `reason` `beyond_horizon` or `entry_budget`, or no reason when it starts within `near_term_lead_min` | both |
| `pending` | Its entry is being written. A begun segment is `pending` until its near-term entry is on the heater | live |
| `programmed` | Its entry is confirmed on the heater and has not fired. A begun segment whose near-term or moved entry has not fired yet is `programmed` | live |
| `merged` | Same state as the segment before it: no entry. It goes with that segment | both |
| `in_force` | It has started and the entry that puts it in force has fired | live |
| `ended` | It had started and a later segment has started | both |
| `failed` | `write_not_confirmed`: a write and its retry were not confirmed. `not_applied_on_device`: read-back found another state | live |
| `removed` | A person deleted the entry that puts it in force. Nothing is written for it, or for the merged segments after it, while the plan is in force | live |

| Reason | Status |
|---|---|
| `beyond_horizon` | `scheduled`: it starts `horizon_h` or more ahead |
| `entry_budget` | `scheduled`: no room within the budget |
| `write_not_confirmed` | `failed` |
| `not_applied_on_device` | `failed` |

Warning: `moved_1_min`, its entry moved past an occupied minute.

Transitions, live:

| From | To | When |
|---|---|---|
| (adopted) | `scheduled`, `pending` or `merged` | As section 6.2 places it |
| `scheduled` | `pending` | It comes within the horizon, room frees, or it begins (near-term entry) |
| `pending` | `programmed` | The write holding its entry is confirmed |
| `programmed` | `in_force` | Its entry's minute |
| `in_force` | `ended` | A later segment starts |
| `scheduled`, `pending`, `in_force`, `ended` | `failed`, `write_not_confirmed` | A write and its retry were not confirmed. Undone by a later confirmed write unless it has ended |
| `in_force`, `ended` | `failed`, `not_applied_on_device` | Read-back differs. Undone if a later entry for it reads back as wanted |
| `programmed` | `removed` | A person deleted the entry that puts it in force before its minute was over |

### 8.3 Commands

Each item of `commands`, in the plan's order: `id`, `command` (as sent; an
empty string if it was not a string), `status`, `reason`, `warnings` (empty
so far), `detail` when there is one, and the command's opaque keys.

| Status | Reason | Meaning |
|---|---|---|
| `rejected` | `invalid_command` or `unsupported_command` | Never sent (section 4.2). `detail` says why |
| `shadow` | | Evaluated, not sent: the mode is `shadow`. Going live sends it |
| `pending` | | To be sent, or sent and not yet reported |
| `applied` | | The heater reports what it set; `demand_response` once sent |
| `failed` | `write_not_confirmed` | It could not be sent, or the library refused it. `detail` is the library's message |
| `failed` | `not_applied_on_device` | The heater did not report the setting within the poll interval plus one minute. `detail` says so if no status came after it was sent |

`rejected`, `applied` and `failed` are final. A failed command is not
retried; send it in a new plan.

### 8.4 Last write reasons

| `reason` | The write |
|---|---|
| `plan` | Adds plan entries, or removes unfired ones |
| `near_term` | Adds a near-term entry |
| `cleanup` | Only removes fired entries |
| `takeover` | The first live write, adding nothing: turns the reservation switch on for the feature's entries |

A write stored by an earlier version may carry another reason.

## 9. Limits

| Limit | Value |
|---|---|
| `intent_id`, segment and command `id` | 1 to 64 characters |
| Segments, commands per plan | No limit in the protocol. Every one is listed on the acknowledgement |
| Timestamps | With a UTC offset; not within 8 days of the limits of year 1 or 9999 |
| Setpoint | A finite number, at most 1000 in magnitude; inside `setpoint` once known |
| Horizon | 144 hours (`horizon_h`) |
| Near-term lead | 2 minutes (`near_term_lead_min`) |
| `entry_limit` | Default 16, 1 to 32. Navien documents 16; the heater tested held 32; larger lists are untested |
| `entry_reserve` | Default 2, less than `entry_limit` |
| Write retry | Once after 60 seconds, then a 15-minute pause |
| Read-back | The poll interval plus one minute after the entry's minute; given up 15 minutes after that |
| Heartbeat | At least every 15 minutes |
| Attribute budget | 15 KiB for the last write and the override entity, under the recorder's 16 KiB |
| Vacation `days` | 1 to 99 |
| Anti-Legionella `period_days` | 1 to 30 |

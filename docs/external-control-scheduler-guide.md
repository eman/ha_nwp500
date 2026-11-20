# Writing a scheduler for external control

This guide is for authors of a scheduler that controls an NWP500 through
this integration's external control feature. A scheduler decides when the
heater should heat and how; the feature writes those decisions into the
heater's own reservation list and reports what happened.

The [protocol reference](external-control-protocol-1.md) is normative: every
key, rule, status and limit is there. The JSON Schema
[`external-control-protocol-1.schema.json`](external-control-protocol-1.schema.json)
describes the plan and every entity's attributes. Setting the feature up is
in [`external-control.md`](external-control.md).

In short, a scheduler:

1. finds the feature's entities by unique id;
2. reads the capability declaration;
3. publishes a plan as the state and attributes of an entity of its own;
4. reads the acknowledgement, and reacts;
5. publishes a new plan whenever its decisions change, and at least daily.

## 1. Find the entities by unique id

Each heater has its own set of entities. Their unique ids are
`<mac>_control_<key>`, for example `<mac>_control_ack` for the plan status.
The default entity ids, such as `sensor.navien_nwp500_plan_status`, are
documented in the reference, but a user can rename an entity, so do not
hard-code them.

From outside Home Assistant, list the entity registry over the WebSocket
API with `{"type": "config/entity_registry/list"}`. Keep the entries whose
`platform` is `nwp500` and whose `unique_id` contains `_control_`. Split
each unique id at `_control_`: the part before is the heater, and the part
after is the key. Each entry's `entity_id` is the id to read states from.

| Key | What you read it for |
|---|---|
| `capabilities` | What the feature accepts and how much room there is |
| `ack` | Whether your plan was accepted, and what became of each segment and command |
| `intent` | Which plan is in force, and its echoed top-level keys |
| `program_hash` | The whole list the feature wants on the heater, the other entries included |
| `programmed_until` | How far the heater's copy of the plan reaches |
| `override` | A person's deletion of one of the plan's entries |
| `heartbeat` | Whether the feature is running, and when it last started |
| `last_write` | The last list write, and whether the heater confirmed it |
| `in_sync`, `next_entry`, `wanted_mode`, `wanted_setpoint` | Checks and displays |

Read the registry again if an entity is renamed: Home Assistant fires
`entity_registry_updated` for that.

## 2. Read the capabilities first

Before planning, read the capabilities entity's attributes (reference,
section 5). Its state is a version hash: re-read the attributes whenever
the state changes.

- **`protocol_versions`.** Rely on a minor version's keys only when the
  version for major `1` is that minor or later, compared as numbers. The
  checks below need `1.4`. Against an older feature, `modes`, `setpoint`
  and `commands` are missing, and a setpoint outside the heater's range
  fails at write time instead of being rejected.
- **`mode`.** `live` writes the heater's list. `shadow` (Preview) works out
  and reports everything but writes nothing. `disabled` (Stopped) adopts no
  plan at all.
- **`modes`.** Use only these in a segment's `mode`.
- **`setpoint`.** The heater's own range, in both units. Keep every
  setpoint inside it, bounds included; outside it, the whole plan is
  rejected (`setpoint_out_of_range`). It is `null` until the heater's
  features are read. Meanwhile plans are not range-checked, so plan within
  a range you know, or wait.
- **`setpoint_resolution_c`.** The heater holds half degrees Celsius. A
  setpoint is converted once, rounding to the nearest half degree (reference,
  section 3.6). Convert your setpoints the same way to know what the heater
  will hold: `135 °F` becomes 57.0 °C, which reads back as 134.6 °F. Two
  setpoints that convert to the same half degree are the same state.
- **`commands`.** Each command's keys, types and ranges.
- **`horizon_h`, `near_term_lead_min`, `entry_limit`, `entry_reserve`,
  `entries_available`.** How far ahead and how many entries can be
  programmed (sections 6 and 7 below).

## 3. Publish a plan

A plan is a JSON document (reference, section 3). Publish it on an entity
your scheduler owns:

- its **attributes** are the document, one top-level key per attribute;
- its **state** is the plan's `intent_id`, so it changes on every plan.

Give every plan a new `intent_id`, and an `issued_at` from a clock that
does not go back: a plan whose `issued_at` is earlier than the plan in
force is rejected (`superseded`). A document with the same `intent_id` and
`issued_at` as the plan in force is taken as that plan received again: it
is still checked, but nothing else it says is adopted.

Do not use Home Assistant's presentation attribute names (`friendly_name`,
`icon`, `unit_of_measurement` and the others listed in the reference,
section 2) as plan keys: they are removed before the document is read.

The user then picks your entity as the **Plan entity** in the integration's
options. Ask them to exclude it from the recorder: its attributes are a
document, not history.

**An MQTT sensor** is the usual way. Publish the plan, retained, to one
topic, and define the sensor by discovery:

```json
{
  "name": "Water heater plan",
  "unique_id": "water_heater_plan",
  "state_topic": "scheduler/water_heater/plan",
  "value_template": "{{ value_json.intent_id }}",
  "json_attributes_topic": "scheduler/water_heater/plan"
}
```

Home Assistant then keeps the latest plan across restarts. A REST sensor or
a template sensor works the same way.

**For a quick test,** post the state to Home Assistant's REST API,
`POST /api/states/sensor.water_heater_plan`, with the `intent_id` as
`state` and the plan as `attributes`. Such a state is gone after a restart:
the feature keeps its stored copy of the plan, and Repairs reports the plan
entity missing until a state is posted again.

## 4. A worked day plan

[`examples/plan-day.json`](examples/plan-day.json) is a day's plan,
published on Sunday at 05:00:12, local time −07:00:

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

- Each segment holds from its `start` until the next one's. `s4`, the last,
  holds until a new plan, so it is a state the scheduler is willing to hold
  indefinitely.
- `s2` and `s4` give no mode, so they keep the mode before them.
- `plan_id` and `purpose` are opaque: the feature echoes `plan_id` on the
  plan ID entity and `purpose` on its segment's acknowledgement.

The heater is in another state when the plan arrives, so the feature
writes one list with four entries:

| Entry | Mode | Setpoint | Why |
|---|---|---|---|
| Sun 05:03 | Heat Pump | 104.9 °F | `s1` has begun: a near-term entry, the first minute at least two minutes ahead |
| Sun 10:30 | Heat Pump | 140.0 °F | `s2` |
| Sun 14:30 | Energy Saver | 134.6 °F | `s3`: 135 °F is 57.0 °C |
| Sun 22:00 | Energy Saver | 104.9 °F | `s4` |

If the heater had already been in Heat Pump at 104.9 °F, `s1` would get no
entry. [`examples/last-write-plan.json`](examples/last-write-plan.json) is
this write as the last write entity reports it.

Once the heater confirms the list, the plan status entity's state is
`programmed`, with these attributes:

```json
{
  "intent_id": "i-20261004T0500-7",
  "reason": null,
  "detail": null,
  "segments": [
    {"purpose": "hold_off", "id": "s1", "status": "programmed", "reason": null, "warnings": [], "fires_at": "2026-10-04T05:03:00-07:00", "in_force": true},
    {"purpose": "charge", "id": "s2", "status": "programmed", "reason": null, "warnings": [], "fires_at": "2026-10-04T10:30:00-07:00", "in_force": false},
    {"id": "s3", "status": "programmed", "reason": null, "warnings": [], "fires_at": "2026-10-04T14:30:00-07:00", "in_force": false},
    {"id": "s4", "status": "programmed", "reason": null, "warnings": [], "fires_at": "2026-10-04T22:00:00-07:00", "in_force": false}
  ],
  "commands": [],
  "rejected": null
}
```

At 05:03 `s1` becomes `in_force`; at 10:30 `s1` becomes `ended` and `s2`
`in_force`, and so on. In Preview every segment's status is `shadow` and
the state is `shadow`. If Home Assistant stops at 06:00, the heater still
runs the 10:30, 14:30 and 22:00 entries on its own.

Publish a segment at least `near_term_lead_min` (2) minutes before its
start. One that starts sooner is `scheduled` with no reason, and gets a
near-term entry once it begins, so it takes effect two to three minutes
late.

## 5. Read the acknowledgement

After publishing, wait for the plan status entity to change. Your document
was:

- **rejected** when `rejected.intent_id` is its `intent_id`. The plan in
  force, if any, stays in force, entries and all;
- **accepted** when the state's `intent_id` attribute is its `intent_id`
  and `rejected` does not name it.

A rejection names one `reason` and a readable `detail`:

| Reason | What to do |
|---|---|
| `invalid_document` | A bug in the document: see `detail` |
| `unsupported_protocol` | Use a major version listed in `protocols` |
| `unordered_segments` | Starts must increase after truncation to the minute: two segments in one minute are not allowed |
| `duplicate_id` | Make every segment and command id unique in the plan |
| `mode_not_allowed` | Use a mode from `modes` |
| `setpoint_out_of_range` | Keep setpoints within `setpoint` |
| `superseded` | Your `issued_at` is older than the plan in force's |

The acknowledgement's state:

| State | Meaning | What to do |
|---|---|---|
| `shadow` | Preview: nothing is written | Nothing; the user chooses Live |
| `pending` | A write is under way, or entries an earlier plan left are still to be removed | Wait. If it lasts, see `last_write` and section 9 |
| `programmed` | Everything the plan needs now is on the heater | Nothing. Segments beyond the horizon or the budget may still be `scheduled`: `programmed_until`'s `complete` says |
| `partly_programmed` | A segment is `failed` or `removed` | Look at the segments |
| `none` | No plan in force | Publish one. While `disabled`, no plan is taken |
| `rejected` | No plan in force, and your document was rejected | Fix it |

Each segment's status and reason:

| Status (reason) | What to do |
|---|---|
| `scheduled` (`beyond_horizon`) | Nothing: it is programmed when it comes within 144 hours |
| `scheduled` (`entry_budget`) | Nothing, unless it matters: it is programmed as earlier entries fire. See section 6 |
| `scheduled` (no reason) | It starts within two minutes: it takes effect a few minutes late. Publish earlier |
| `pending`, `programmed`, `in_force`, `ended`, `merged` | Normal. `merged` repeats the state before it and needs no entry; give it `reassert: true` if it must have one |
| `failed` (`write_not_confirmed`) | The heater did not confirm the list. The feature keeps trying (section 9) |
| `failed` (`not_applied_on_device`) | After the entry, the heater reported another mode or setpoint: a mode held by a TOU window, Vacation, or a person's change. Decide whether to plan around it |
| `removed` | A person deleted the entry (section 8) |

A warning `moved_1_min` means another enabled entry held that minute, so the
entry fires a minute or more late.

## 6. The entry budget

The heater's list is small, and it holds entries that are not the
feature's: the owner's, anyone's. They stay as they are and use room.

- At most `entry_limit − others − entry_reserve` plan entries are on the
  list at once, where `others` is every entry that is not the feature's,
  switched on or off. Count them in the plan fingerprint's `entries` with
  `owner: "foreign"`.
- `entry_reserve` entries are kept free for near-term entries.
- Segments are programmed in time order. The rest wait as `scheduled`,
  `entry_budget`, and are programmed as earlier entries fire and are
  removed.
- `entries_available` is the room left now, after every entry the list
  holds, the feature's own included.

**Example.** `entry_limit` is 16 and `entry_reserve` 2, and the heater holds
5 entries of the owner's. Before any plan, `entries_available` is
16 − 5 − 2 = 9.

The scheduler publishes at 05:00 a plan of 12 segments: `s0`, already
begun, then `s1` to `s11`, two hours apart from 08:00, alternating between
two setpoints, so none merges.

- `s0` gets a near-term entry at 05:03, from the reserve.
- `s1` to `s9` get the 9 plan entries there is room for.
- `s10` and `s11` are `scheduled`, `entry_budget`.
- The list now holds 5 + 10 = 15 entries, so `entries_available` is 0.
- `plan_written_up_to` is `s10`'s start, with `complete: false` and
  `scheduled: 2`. The acknowledgement is `programmed`.

At 08:00 `s1` begins. The next write removes the fired entries of `s0` and
`s1` and adds `s10`, and only `s11` waits.

To keep a plan inside the budget, give a run of identical states one
segment (the feature merges them anyway), use `reassert` only where it
matters, and keep in mind that every segment within the horizon wants an
entry.

## 7. Republish daily

A plan has no end: its last segment holds until a new plan. The heater's
list can hold only what starts within the next 144 hours, and the feature
programs later segments as time passes while it runs. If Home Assistant is
down, the heater runs what is programmed, then holds the last programmed
state, and a week after each entry fired it fires again.

So publish a new plan at least once a day, and whenever your decisions
change, so the plan always covers the coming days. Segments that start
within the horizon are all on the heater, budget permitting, and run even
if Home Assistant stops; later ones wait as `beyond_horizon`. A plan that
is not renewed runs out, and its last segment becomes the heater's state
indefinitely. Each new plan needs a new `intent_id`
and a later `issued_at`. Keep the `id`, state and start of segments that
have not changed: an entry already on the heater for the same segment id,
state and minute is kept, not written again. A plan that changes nothing
writes nothing, unless the heater reports another state than the segment
in force: the new plan then puts that state back.

Leave out the commands you have already sent: see below.

### Commands

`commands` (protocol 1.3) sets what a reservation entry cannot: Vacation,
power, Anti-Legionella, the TOU switch and demand response
([`examples/plan-commands.json`](examples/plan-commands.json)).

- **Every adopted plan sends all its commands**, once each, in order, after
  its list is written. A daily plan that copies yesterday's commands sends
  them again. Put a command only in the plan that should send it.
- A plan in force received again, or restored after a restart, sends
  nothing again.
- Read each command's status on the acknowledgement's `commands`. A
  `failed` command is not retried: to try again, put it in a new plan.
- A rejected command (`invalid_command`, `unsupported_command`) does not
  stop the plan.
- If one command must take before the next, send the next in a later plan.
- In Preview a command's status is `shadow`; it is sent when the feature
  goes live.

[`examples/ack-commands-live.json`](examples/ack-commands-live.json) and
[`examples/ack-commands-shadow.json`](examples/ack-commands-shadow.json)
are the acknowledgement of `plan-commands.json`, live and in Preview. Its
`recirculation` command is not one the feature knows, to show a command
rejected on its own, so that example does not validate against the schema.

## 8. Deletions and people's changes

The feature reports what people do to the plan's entries; whether to honour
or overrule them is the scheduler's decision.

- **A deleted entry.** If a person deletes the entry that puts a segment in
  force before its minute is over, the segment is `removed`, and the plan entry
  removed entity turns on, with the removed entry as `value` and the
  segment as `segment`. Nothing is written for that segment, or for merged
  segments after it, while the plan is in force.
  - To honour it, publish a plan that leaves that segment out, or holds the
    state the person wanted.
  - To overrule it, publish a new plan. A new plan is programmed as it
    stands: it writes the entry again, and the report ends.
- **A changed setpoint or mode.** The feature does not report it. Read the
  heater's own entities. After an entry, a difference is
  `not_applied_on_device`. A new plan whose segment in force differs from
  what the heater reports puts the segment's state back. A segment with
  `reassert: true`, such as a nightly one, ends a person's change at its
  start.
- **The reservation switch turned off.** No entry fires, and the feature
  keeps it off. The Reservation Schedule sensor's `enabled` reports it.
- **An added entry** is not the feature's. It fires as set and uses room in
  the budget.

The plan mode now and plan temperature now entities show the state of the
segment in force by time, even after a person removed its entry; their
`segment` attribute is then `null`.

## 9. Restarts, failures and retries

**The heartbeat.** Its state moves at least every 15 minutes while the
feature runs. An older state means the feature, or Home Assistant, is not
running: the heater keeps running what is programmed.

**Restarts.** `started_at` changes when the feature starts: a Home
Assistant restart or a reload of the integration, such as an options
change. After a start:

- the stored plan is restored. Its segment in force is not written again,
  and its commands are not sent again;
- if your plan entity holds a different plan, it is received as any new
  document;
- `entries_available` and `setpoint` are `null`, and the plan fingerprint
  unknown, until the heater's list and features are read;
- re-read the capabilities: their version may have changed.

There is no need to republish because of a restart. Check that the plan ID
entity's state is your latest plan; if your plan entity lost its state,
publish again.

**Write failures (live).** Every list write must be confirmed by the
heater reading the list back.

1. An unconfirmed write sets `last_write`'s `confirmed` to `false`, and is
   retried once after 60 seconds.
2. If the retry is not confirmed either, the segments it served are
   `failed`, `write_not_confirmed`, and writing pauses for 15 minutes.
3. After the pause the feature tries again, and so on. A confirmed write
   clears the failure, except for a segment that has ended meanwhile.

A plan published during the pause is adopted at once and written when the
pause ends; it does not cut the pause short. Commands are sent whether or
not the list write was confirmed.

## 10. Several heaters

Every heater on the integration's config entry follows the one plan
entity: one plan for all of them. Each heater checks the plan on its own,
against its own range, programs its own list and reports on its own
entities. So the same document can be accepted by one heater and rejected
by another, for example `setpoint_out_of_range`. Group the entities by the
part of the unique id before `_control_`, and read every heater's
acknowledgement.

## 11. Stopping

- **Withdraw the plan:** publish a plan with `"segments": []`
  ([`examples/plan-stop.json`](examples/plan-stop.json)). Every entry of the
  feature's is removed, and the heater keeps the state it is in. The
  acknowledgement is `pending` until they are gone. Do this before the user
  turns the feature off, or its entries stay on the heater, firing every
  week.
- **Stop applying plans:** the Stop external control button, or the Stopped
  mode, writes nothing. What is on the heater stays there.

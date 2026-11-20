# External control

An optional feature that lets an external scheduler control the NWP500
through Home Assistant. The scheduler publishes a **plan**: a timeline of the
states the heater should be in. The feature programs that plan into the
heater's own weekly reservation list, so the heater carries it out itself and
keeps following it if Home Assistant, the feature or the scheduler becomes
unavailable.

**The feature applies your scheduler's plan and reports the result.** It
reads only the heater and makes no decision of its own. It does nothing
without a scheduler of your own: an automation, a Node-RED flow or a
script.

This page is about setting the feature up. For writing a scheduler:

- the [scheduler guide](external-control-scheduler-guide.md): how to find
  the entities, publish a plan and read the result;
- the [protocol reference](external-control-protocol-1.md): every key,
  rule, status and limit, normative;
- the JSON Schema
  [`external-control-protocol-1.schema.json`](external-control-protocol-1.schema.json),
  and sample plans in [`examples/`](examples/);
- the [design record](external-control-design.md): the reasoning, the
  device tests and the history, also published as
  [issue #158](https://github.com/eman/ha_nwp500/issues/158).

The options form and the entities use plain names; the protocol keeps its
own. A **plan** is the protocol's *intent* (its id is `intent_id`), and a
**plan step** is a *segment*.

## Enabling the feature

Turn it on when you add the integration (the sign-in form's folded
**Optional features** section), or later in the integration's options
(Settings > Devices & services > Navien NWP500 > Configure > External
control). Its page holds:

| Option | Default | Notes |
|---|---|---|
| External control | off | The toggle, on the options page only |
| Plan entity | none | Required while it is on. The entity your scheduler puts its plan on. If only one entity's attributes carry a plan, it is chosen for you |
| Mode | Preview | See the table below |
| Schedule size limit / entries kept free | 16 / 2 | Folded under **Schedule size**. The most entries the heater's schedule may hold in total, **your own included**, and how many are kept free for changes needed right away. The feature uses what is left after your entries and the ones kept free. Navien documents 16; the heater tested held 32, and larger lists are untested. The limit goes up to 32, and the entries kept free must be fewer than it |

| Mode | Stored as | What it does |
|---|---|---|
| Preview: show what the plan would do, write nothing | `shadow` | Works out the schedule and shows it on the entities; writes nothing |
| Live: follow the plan | `live` | Writes the plan into the heater's schedule, as your scheduler sends it |
| Stopped: stop applying plans, write nothing | `disabled` | No plan is adopted and nothing is written. What is on the heater stays there. Its entities stay; turning the toggle off removes them |

Changing any option reloads the integration and updates the capability
entity, and so its version. Options of earlier versions are removed the
next time the form is saved: the live switches, the surplus sensor,
threshold and minimum run, the faster-recovery mode, the lowest/highest
temperature, the allowed modes and the saved copy of your own settings. An
earlier version's `live` with its segments switch off writes nothing, and
shows as Preview, until then.

The constant `CONTROL_LIVE_AVAILABLE` in `const.py` is a kill switch: set to
`False`, the options form stops offering Live and a `live` option runs as
Preview.

While the feature is off, nothing of it loads: no imports, listeners,
entities, stored data, timers or writes. Turning it off again removes its
entities and deletes its stored data, and writes nothing to the heater:
the entries your plans put in its schedule stay there, firing every week.
With its stored data gone, the feature no longer knows those entries were
its own, so turned on again it counts them as yours, using up schedule
room. To clear them first, have your scheduler publish a plan with no
segments (`segments: []`) while in Live, then turn the feature off.

## The plan entity

Your scheduler publishes each plan on an entity of its own, such as an MQTT
sensor: its state is the plan's id and its attributes are the plan. The
[scheduler guide](external-control-scheduler-guide.md#3-publish-a-plan)
shows how. Choose that entity as **Plan entity**.

- Exclude the plan entity from the recorder: its attributes are a document,
  not history.
- Renamed in Home Assistant, the plan entity is followed: the option moves
  to its new id and the integration reloads.
- Missing from Home Assistant once it has started, Repairs says so until it
  is back. Meanwhile the plan in force stays in force and no new plan is
  taken.
- The feature stores the last plan it accepted, so a restart does not lose
  it.
- Every heater on the integration's account follows the same plan entity.

## The entities

Each heater gets these entities. The four marked *diagnostic* are under the
device's Diagnostic section; they stay enabled. What each one reports, in
full, is in the [reference](external-control-protocol-1.md#7-entities).

| Entity | Shows |
|---|---|
| External control capabilities (diagnostic) | What the feature accepts, and how much room the schedule has |
| Plan ID | The plan in force |
| Plan status | Whether the latest plan was accepted, and what became of each step: Preview only, programmed, waiting, failed |
| Plan fingerprint (diagnostic) | The schedule the feature wants on the heater, your own entries included |
| Heater matches plan | On when the heater's schedule is the one the feature wants |
| Plan written up to | How far ahead the heater's copy of the plan reaches |
| Plan next change | When the feature's next entry fires, and what it sets |
| Plan mode now, Plan temperature now | The state the plan wants now |
| External control last write (diagnostic) | The last schedule write: what it added and removed, and whether the heater confirmed it |
| Plan entry removed | On while someone's deletion of one of the plan's entries is reported |
| External control heartbeat (diagnostic) | Moves at least every 15 minutes while the feature runs |
| Stop external control (button) | Sets Mode to Stopped: the feature stops applying plans |

## Preview and Live

**Preview** (`shadow`) is the default. The feature reads the heater, works
out the schedule it would write, and shows it on the entities as if it had
written it. Each write it would make is on the last write entity with
`simulated: true`. Nothing is written to the heater, and the plan's device
commands are not sent.

**Live** writes the plan into the heater's reservation list:

- **Taking the list over.** The first write of the feature's own entries
  turns the reservation switch on. A plan with none, such as one that
  stops, leaves the switch as it is. Every entry that is not the feature's,
  yours included, stays exactly as it is and fires as you set it.
- **Every write** reads the list first, keeps the entries that are not the
  feature's, writes the list whole, and counts only once the heater reads
  the new list back. An unconfirmed write is retried after a minute, then
  after a 15-minute pause.
- **Each entry is checked** after its minute: the heater's mode and
  setpoint are compared with the entry's, and a difference is reported.
- **Your changes.** An entry you delete is not written again while its plan
  is in force, and the Plan entry removed entity reports it. Your
  scheduler's next plan decides: a new plan is programmed as it stands. If
  you turn the reservation switch off, it stays off.
- **Going live** on a heater that holds nothing of the feature's programs
  the plan in force afresh, and sends its device commands.
- **Leaving Live, Stopped, or turning the feature off** writes nothing.
  What the plans put on the heater stays there, and keeps firing every
  week, until your scheduler's next plan or you change it.
- **A feature that cannot start** leaves the rest of the integration
  running, and raises a Repairs issue until it starts or is turned off.

## Auditing Preview

In Preview the program entities show the list the feature would write, and
the last write entity shows each write it would have made. Compare the
Plan fingerprint with the heater's own Reservation Schedule sensor: Heater
matches plan is off whenever they differ, which in Preview means the plan
has entries of its own. Plan status shows each step's status, when its
entry fires, and any warning. Once that looks right, switch Mode to Live.

## Behaviour in brief

- **Every write is a possible start.** Outside a TOU window, a setpoint left
  above the upper tank started the compressor within about 30 seconds in 112
  of 117 writes, whether it came from an entry or directly.
- **An entry's mode does not take effect inside a TOU window**; its setpoint
  does. The mode is held, and applied when the window ends. When the
  windows fall is your scheduler's to know.
- **The heater fires an entry whatever the compressor is doing.** Cycle
  policy, such as a minimum run before stopping, is the scheduler's: it
  chooses the plan's times.
- **Vacation, power-off and Anti-Legionella get no special handling.** The
  feature writes the plan whatever state the heater is in. The heater skips
  entries during Vacation and fires them while powered off, powering it
  back on. What to do about someone putting the heater in Vacation or
  powering it off is your scheduler's.
- **Unload and restart write nothing.** The programmed entries keep running.
- **If Home Assistant is down,** the heater runs the entries already
  programmed, up to 144 hours ahead, then holds the last state. Entries that
  have fired stay on the heater, so a week later it repeats them.

Device behaviour these rules rest on is documented in
[`nwp500-python`](https://github.com/eman/nwp500-python): *What starts a
recovery*, *The TOU recovery cap*, and *Reservations and mode writes during
a TOU window* in the scheduling how-to.

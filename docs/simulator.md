# Hot water simulator

The hot water simulator models your water heater's tank from what the heater
reports. It answers these questions, kept up to date as sensors:

- Is the tank heating now, or will it heat within the next 24 hours?
- When does the tank's current or next heating end?
- How much electricity is the heater likely to use over the next 24 hours?
- How much hot water can the tank deliver now, for how many showers, and
  is a bath ready?

It runs the simulator from [nwp500-python](https://github.com/eman/nwp500-python)
(`nwp500.sim`) inside Home Assistant.

## Turning it on, or not

The simulator is optional, and off unless you turn it on.

- **When you add the integration**, after you sign in, you choose either
  **Set up the hot water simulator** or **Skip for now**. Skipping sets the
  integration up exactly as it was before the simulator existed.
- **Later**, open the integration's options and turn **Hot water simulator**
  on or off. It can be turned on only while the integration is running,
  since its water heaters are known only then.

Either way, you then choose the sensors beside each water heater (see
[Inputs](#inputs)). Saving the options again lets you change or clear them.

The simulator needs numpy, pandas and scipy. When you turn it on, the
integration checks for them and installs any that are missing, showing
progress while it does. Home Assistant already includes numpy; pandas and
scipy are usually installed then, which can take a few minutes on slower
hardware. With the simulator off, none of them is installed or loaded.

After a Home Assistant update the packages may need installing again. The
simulator then starts once they are installed; the water heater itself
doesn't wait for it.

If the packages can't be installed, you can try again or go on without the
simulator:

- **If Home Assistant is set not to install packages** (`skip_pip`),
  install them yourself, then try again.
- **After a failed install**, Home Assistant won't try again until it
  restarts, so restart it first.

If the simulator is on but can't start, Repairs says why: the packages are
missing, their install failed (restart Home Assistant), or something else
went wrong (the log has it; reload the integration to try again). The rest
of the integration runs either way.

Turning the simulator off removes its sensors. The simulator stores
nothing of its own.

## Inputs

For each water heater you can choose these. Each is optional: the
simulator answers from the heater alone, and each input adds what it
measures.

| Input | Used for | Without it |
|---|---|---|
| Air temperature beside the heater | How fast the heat pump heats: it takes its heat from this air | The heater's own reading of the air at its intake |
| Humidity beside the heater | The heat pump's output | 50% |
| Weather forecast | The air beside the heater over the next day, projected from a weather entity's hourly forecast. The library learns how that air follows the outdoor air from the last 3 days' history: the air sensor's, or without one the heater's own reading while its heat pump runs | The air held at its reading now |
| Cold water supply temperature | With the hot water in use sensor, the mains' temperature, read from a probe on the cold supply over the last 72 hours' long draws | The library's default inlet |
| Hot water in use | A binary sensor on while the household draws hot water, its circulation's water not counted: whether water is being drawn now, and which of the supply probe's readings are long draws | No draw now, and no mains' temperature from the probe |
| Circulation pump speed | The circulation's runs, left out of the supply probe's long draws | No runs left out: the probe and the hot water in use sensor still give the mains' temperature. A pump configured whose history can't be read gives none |
| Hot water draw flow | The household's hot water draw flow now, the circulation's water not in it, and 0 while nothing is drawn: the heat book (below) | No book: the hot water is judged from the tank's probes |
| What the draw flow counts | Delivered water, measured at the taps after any mixing valve, or tank water, measured on the heater's hot outlet before the valve (the library's `flow_volume`). A tank-water gallon carries the tank's heat, more than a delivered one. The library's default empty tank was fitted to delivered water: for tank water, fit it to your heater with nwp500-python's calibration | Delivered water |
| Bath size, shower size | The hot water a bath and a shower take, in Home Assistant's volume unit | The library's sizes, which `defaults_used` names |
| Mixing valve temperature | What the heater's mixing valve delivers, in Home Assistant's temperature unit. A tank hotter than it gives more hot water, mixed down with cold: the walks, the book and the hot water sensors price each delivered gallon at it (the library's `delivery_intercept_f`, with no slope). The actions use it for the heater's parameters, not a document's own | No valve: water is delivered at the tank's temperature |

The air's fit to the outdoor air is made at most once an hour, from the
recorded history of the air sensor (or of the heater's own air and
compressor entities) and of the weather entity, and kept for the
simulations between. Without an air sensor, enable the heater's Ambient
temperature entity, which is off by default: a disabled entity records
nothing to fit on. Either way the fit waits for a day of recorded
history.

Between fits, each simulation is given the kept fit: the library repeats
its note, anchors the projection to the air reading beside the heater (or
the heater's own newest reading while its heat pump ran, under an hour
old), and the fit's arithmetic runs outside Home Assistant's event loop.
After a fit that failed, the simulations until the next attempt repeat
the library's notes on why.

Where the library can't fit one, such as with less
than a day of history or air that doesn't follow the outdoor air, its
notes say why, the air is held at its reading, and the fit is tried again
an hour later.

The mains' temperature is read from Home Assistant's recorded history, at
most once an hour, so the probe, the hot water in use sensor and the pump,
when there is one, must be recorded (the recorder's default). Whether water is drawn now is
read with every simulation. A probe that holds one value through a long draw
leaves one recorded reading, too few for the library to read the mains
from; its notes say so.

A sensor that has not reported for an hour is not used. One that keeps
reporting the same value is.

Don't choose the heater's own ambient temperature sensor as the air sensor:
the simulator already falls back to it. While the heat pump is off, it reads
the cabinet, several degrees warm: the library takes 6.2 °F off an idle
reading (`sim_adapters.IDLE_CABINET_WARM_F`, one installation's mean, 9.3 °F
in February to 4.8 in October), and its `ambient_from_idle_cabinet` reason
says so. A sensor in the room is still better.

The simulator also reads, from the integration itself, the tank's
temperatures, the mode and setpoint, the heater's reservation schedule and
its time-of-use schedule.

The heater's parameters are the library's defaults for its tank's size
(`nwp500.sim_adapters.params`). The heater is read as the library's own live
source reads it (`nwp500.sim_live`). The heating history before now (when
the compressor last stopped, when the tank last heated) is left unset, as
the library leaves it for a one-shot read, and `defaults_used` names the
defaults the simulator used instead.

## Sensors

| Sensor | State |
|---|---|
| Simulated heating | `heating`, `scheduled` (will heat within 24 hours), `idle` (won't), or `disabled` (Vacation or powered off) |
| Simulated hot water ready | When the heating the tank is in, or the next one within 24 hours, ends, as the library's `RecoveryResult.ready_at` gives it. Its `stop` attribute says why it ends: `target` (the tank is heated), or another, such as `mode` (a mode change) or `cap` (a TOU period's cap). Unknown when it won't heat within 24 hours, or when the 24 hours run out before the heating ends (`stop` is `horizon`) |
| Simulated energy, next 24 hours | kWh. A forecast, not a meter: it has no state class, so it stays out of long-term statistics and the Energy dashboard |
| Simulated hot water available | The hot water the tank can deliver now, in whole gallons or litres, as the library's `time_to_ready` says |
| Simulated showers ready | Whole showers the tank has hot water for now. Its `shower_gal` attribute is a shower's size |
| Simulated bath ready | On when the tank can fill a bath now. Its attributes: `bath_gal`, `margin_gal` (whole gallons), and `ready_at`, when the heat pump has it ready if not now |

The hot water sensors' `reasons` and `defaults_used` are `time_to_ready`'s.

### The heat book

With a hot water draw flow, nwp500-python books the tank's heat
(`HomeAssistantBook`): from the heater's own entities' recorded history
and the flow's, over the last 72 hours, it finds when the tank's heat was
last known and walks it forward through each draw and recovery since.
The simulations and the hot water sensors are given that book, so the
gallons are what is left in the tank, not only what its two probes show.
The circulation pump's speed, the cold water supply probe and the hot
water in use sensor, when chosen, add their part: the circulation's runs,
and the gallons converted at the mains' temperature.

- **The flow.** The household's draws only, without the circulation's
  water, as water is delivered at the taps (after a mixing valve, where
  there is one): the library prices a delivered gallon at the valve's
  delivery line. 0 while nothing is drawn. Its unit is any flow unit
  Home Assistant offers (gal/min, L/min, L/h, m³/h). Each value holds
  until the next, so a source that stops updating while it still shows a
  value is read as that value going on: the flow's source should go
  `unavailable` when it stops.
- **Across an outage.** Where the heater wasn't heard from or an entity
  was `unknown` or `unavailable`, such as through a Home Assistant
  restart, the library walks the book across the gap, each value held
  from before it: the flow for a minute and the pump's speed for five,
  then none. The book is not lost to a restart.
- **Recorded history.** The book reads the flow's and the heater's
  recorded history, so the flow and the heater's entities must be
  recorded (the recorder's default). The heater is heard from by its
  Last status sensor. The first simulation reads 72 hours of history;
  each one after reads only what is new.
- **Anchors.** The book is anchored where the tank's heat is known from
  its probes: when a recovery ends, and while the heat pump runs alone
  with both probes alike, nothing drawn, once it has run 15 minutes
  since the last draw.
- **Without a book**, such as with no draw flow, after a recorder read
  fails until one succeeds, or before the first anchor, the hot water
  is judged from the tank's two probes, with `no_heat_book` among its
  reasons: the tank's heat as the probes place it, whatever they read,
  and the larger of that and the library's evidence where it has some.
  The notes say why there is no book.
- **A late flow reaches the book late.** A draw that the flow reports
  after it happened is booked at the next simulation after it is
  recorded.

The actions use the heater's book for a scenario on the heater's
(`use_heater`) that still starts now with the heater's parameters; for
another start or tank, the book is left out and a note says so.

Each sensor has these attributes:

- `reasons` lists why the answer may be less than it seems, such as `no_heat_book`, `ambient_defaulted` or `reservations_unknown`.
- `defaults_used` lists each default the answer used, as `name=value`.
- `notes` says what the air and humidity rest on.

Simulated heating also has the next heating interval's start (`starts_at`, none while it is heating), its `source` (compressor or element), `cause`, `trigger`, `energy_kwh`, `tou_capped` and `confidence`. Energies are to a tenth of a kWh, so an answer that hasn't changed adds nothing to history.

`reasons`, `defaults_used` and `notes` are not recorded in history.

The sensors appear once the simulator is running, which can take a few
minutes while its packages install. They are unavailable while the heater
isn't reporting and when a simulation fails (the log says why, once). In
Standby the library walks the heater as in Power Off, as the NaviLink app
counts it: nothing heats.

Nothing optional holds them up. Until the heater's details have arrived,
the tank size is unknown and the library's defaults are used, which its
notes say. Until the heater's time-of-use plan has been read, the walk
goes on without it and the library's reasons say `tou_schedule_unknown`.

The simulation runs again when the heater reports, at most once a minute,
and every five minutes in any case.

## Actions

The simulator's actions return the library's answers on request and change
nothing on the heater. Each is one of nwp500-python's calls on a scenario:

| Action | The library's call | `result` |
|---|---|---|
| `nwp500.get_scenario` | none: the scenario the others walk | a scenario document |
| `nwp500.simulate` | `sim.simulate` | a result document |
| `nwp500.time_to_ready` | `sim.time_to_ready` | `kind: readiness` |
| `nwp500.next_recovery` | `sim.next_recovery` | `kind: recovery` |
| `nwp500.forecast` | `sim.forecast` | `kind: forecast` |
| `nwp500.heater_profile` | `sim.profile.heater_profile` | `kind: heater_profile` |

They need the simulator running. While they read the heater, they refuse
to answer before the heater has reported its status.

### The scenario

Every action takes:

| Field | Meaning |
|---|---|
| `device_id` or `entity_id` | The water heater |
| `scenario` | Optional. A scenario document (nwp500-python's `nwp500.sim.codec`, `schema_version` 1), laid over the heater's scenario as `nwp-cli simulate` lays one: objects merge key by key, and any other value, a list included, replaces the heater's. A `params` section replaces the parameters for the heater's tank size |
| `use_heater` | Start from the heater's scenario now. Default on. Off, the document alone is the scenario, and it must give all of it |

So a planner can get the heater's scenario from `get_scenario`, change it
(a setpoint, a mode, a reservation schedule, the household's draws, another
tank's parameters), and ask any action about it. The documents are in the
library's units: °F, Wh, US gallons, and ISO 8601 times and durations
(`PT6H`). A document's times need a time zone or an offset: the library
refuses one without.

The scenario starts now, and runs 24 hours, unless the document says
otherwise. Nothing walks more than 168 hours. Without parameters from the
document or the heater (with `use_heater` off and no `params`), the library
uses its defaults for a 65 gal tank, and `inputs.notes` says so.

```yaml
action: nwp500.get_scenario
data:
  entity_id: water_heater.heater
response_variable: snapshot
```

### Each action's fields

- `simulate`:
  - `hours`: how far ahead, 1 to 168, in place of the document's.
  - `writes`: setpoint or mode changes to try, in place of the document's
    (an empty list clears them; left out, the document's stand).
    Each has `at` (when, within the walk), and `temperature` (in Home
    Assistant's unit, 104.9-149.9 °F or 40.5-65.5 °C), `mode`
    (`heat_pump`, `electric`, `energy_saver`, `high_demand`, `vacation` or
    `power_off`), or both. The library doesn't model a change into
    Vacation or Power Off: it leaves that write out and its `reasons` say
    `write_mode_not_modelled`.
  - `include_points`: also return the tank's path. Default off.
- `time_to_ready`: `bath_gal` and `shower_gal`, a bath's and a shower's
  volume in US gallons. Without them, the library's, which its `reasons`
  name.
- `forecast`: `target_time`, when the tank must be ready (required, at most
  168 hours after the scenario's start), and `target_f` or, for a partial
  charge, `target_lower_f`, in °F. Without them, the heater's stop
  threshold, else its setpoint.

In these fields, unlike in a document, a time without a time zone is taken
in Home Assistant's.

```yaml
action: nwp500.simulate
data:
  entity_id: water_heater.heater
  hours: 12
  writes:
    - at: "2026-10-21T14:00:00"
      temperature: 130
      mode: heat_pump
response_variable: simulation
```

### The response

Each response has two parts:

- `result` is the library's document, in its units, with its
  `schema_version`. `simulate`'s is the result document; its fields are
  `start`, `until`, and:
  - `heating`: each heating interval, with its start and cause, end, stop,
    source, target and energy.
  - `writes`: setpoint writes and reservation firings, as they would land.
  - `mode_writes`: mode writes, as they would land.
  - `modes`: the mode over time.
  - `windows`: the scenario's hot water draw windows as the walk found
    them. They are the document's: the heater gives none. They say nothing
    about time-of-use windows.
  - `points` and `delivery`: the tank's path and the delivered water's
    temperature at each point. They are left out unless `include_points`
    is on: the path is long.
  - `reasons` and `defaults_used`, the library's own.

  The others' carry a `kind` and the library answer's fields
  (`nwp500.sim.codec.to_document`).
- `inputs` holds the integration's notes on what it gave the library:
  `reasons` (such as `reservations_unknown`, when the heater's reservation
  schedule hasn't been read yet) and `notes` (what the air and humidity
  rest on, and the library's own notes on the scenario).

A scenario the library refuses as a scenario, such as a document it can't
read or a write outside the walk, is the caller's mistake and says so. An
answer the library can't give, such as for a setpoint out of the heater's
range, fails with the library's message.

## Accuracy

These answers come from physics with the default parameters for the tank's
size. Expect:

- **Long recoveries run short.** Physics alone says a long recovery ends
  sooner than it does. At one installation, recoveries with more than three
  hours to go ended about three quarters of an hour later than physics said.
- **The air matters most.** Without an air sensor, the answer rests on the
  heater's own reading, which runs warm while the heat pump is off. Without
  a weather forecast, the air is held at its reading for the whole day.
- **Without a heat book the hot water is judged from the tank's probes.**
  Without a draw flow (`no_heat_book`), the gallons are the heat the two
  probes place in the tank. Two probes can't see where the hot water ends
  between them, so a tank with its probes apart reads less surely than a
  booked one.

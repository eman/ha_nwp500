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
| Bath size, shower size | The hot water a bath and a shower take, in Home Assistant's volume unit | The library's sizes, which `defaults_used` names |

The air's fit to the outdoor air is made at most once an hour, from the
recorded history of the air sensor (or of the heater's own air and
compressor entities) and of the weather entity, and kept for the
simulations between. Without an air sensor, enable the heater's Ambient
temperature entity, which is off by default: a disabled entity records
nothing to fit on. Either way the fit waits for a day of recorded
history. Where the library can't fit one, such as with less
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
the cabinet, several degrees warm, so a sensor in the room is better.

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
| Simulated hot water ready | When the heating the tank is in, or the next one within 24 hours, ends, as the library's `RecoveryResult.ready_at` gives it. Its `stop` attribute says why it ends: `target` (the tank is heated), or another, such as `horizon` (the 24 hours ran out) or `mode` (a mode change). Unknown when it won't heat within 24 hours |
| Simulated energy, next 24 hours | kWh. A forecast, not a meter: it has no state class, so it stays out of long-term statistics and the Energy dashboard |
| Simulated hot water available | The hot water the tank can deliver now, in whole gallons or litres, as the library's `time_to_ready` says |
| Simulated showers ready | Whole showers the tank has hot water for now. Its `shower_gal` attribute is a shower's size |
| Simulated bath ready | On when the tank can fill a bath now. Its attributes: `bath_gal`, `margin_gal` (whole gallons), and `ready_at`, when the heat pump has it ready if not now |

The hot water sensors' `reasons` and `defaults_used` are `time_to_ready`'s.
The tank's hot water is judged from its two probes until the simulator
books the tank's heat (planned): where the tank is colder than every draw
the library's evidence holds, it is not known to have any hot water, and
it reads 0 with `colder_than_all_evidence` among its reasons.

Each sensor has these attributes:

- `reasons` lists why the answer may be less than it seems, such as `no_heat_book`, `ambient_defaulted` or `reservations_unknown`.
- `defaults_used` lists each default the answer used, as `name=value`.
- `notes` says what the air and humidity rest on.

Simulated heating also has the next heating interval's start (`starts_at`, none while it is heating), its `source` (compressor or element), `cause`, `trigger`, `energy_kwh`, `tou_capped` and `confidence`. Energies are to a tenth of a kWh, so an answer that hasn't changed adds nothing to history.

`reasons`, `defaults_used` and `notes` are not recorded in history.

The sensors appear once the simulator is running, which can take a few
minutes while its packages install. They are unavailable while the heater
isn't reporting and when a simulation fails (the log says why, once). They
are also unavailable in Standby, which the library doesn't model.

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
to answer in Standby, which the library doesn't model, and before the
heater has reported its status.

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
- **The hot water is judged from the tank's probes.** Without a heat book
  (`no_heat_book`), the gallons rest on the library's evidence for the
  tank's two temperatures, and read 0 for a tank colder than all of it.

## Planned

- A heat book from a household hot water flow, when one is configured.

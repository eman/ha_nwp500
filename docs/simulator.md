# Hot water simulator

The hot water simulator models your water heater's tank from what the heater
reports. It answers these questions, kept up to date as sensors:

- Is the tank heating now, or will it heat within the next 24 hours?
- When does the tank's current or next heating end: when does it come to
  temperature?
- When does the heat pump's compressor next start?
- How do the tank's upper and lower temperatures move over the next day:
  rising while it heats, falling while it stands? The
  [tank forecast card](#the-tank-forecast-card) draws them.
- How much electricity is the heater likely to use over the next 24 hours?
- How much hot water can the tank deliver now, for how many showers, and
  is a bath ready?

It runs the simulator from [nwp500-python](https://github.com/eman/nwp500-python)
(`nwp500.sim`) inside Home Assistant.

## Turning it on, or not

The simulator is optional, and off unless you turn it on.

- **When you add the integration**, the sign-in form has a folded
  section, **Optional features**: turn **Hot water simulator** on there.
  Left folded, the integration is set up exactly as it was before the
  simulator existed, with no page after the sign-in.
- **Later**, open the integration's options and choose **Hot water
  simulator**, then turn it on or off. It can be turned on only while the
  integration is running, since its water heaters are known only then.

Either way, a page for each water heater follows, and every field on it
is optional (see [Inputs](#inputs)): the sensors beside the heater
first, then two folded sections, **Hot water use** and **Baths, showers
and mixing valve**. Left empty, the sizes are the defaults. The first
time, the only weather entity is suggested, and, turned on from the
options once the heater has an area, the only temperature and humidity
sensors in that area besides the heater's own. Choosing **Hot water simulator** in the options again
changes or clears them; the update interval and external control have
their own pages, and saving one doesn't walk the others.

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
  restarts, so the only choice is to go on without the simulator: restart
  Home Assistant, then turn it on in the integration's options.

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
| Bath size, bath refill size, shower size | The hot water a bath, a top-up of a filled bath, and a shower take, in Home Assistant's volume unit | The library's bath (28.7 gal, 109 L: the larger of two metered hot-only fills of one household's bath) and shower (12 gal, 45 L, assumed), which `defaults_used` names; a refill of 10.2 gal (39 L), the largest of that household's metered top-ups (4.5 to 10.2 gal, 15 to 90 minutes after the fill), as the library's `ReadinessParams.top_up_gal` will have it |
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
| Simulated hot water ready | When the tank comes to temperature: the end of the heating it is in, or of the next one within 24 hours, when that heating ends at its target. Heatings one after another are one: in Electric, the upper element's and then the lower's. Its `stop` attribute says why the heating ends: `target` (the tank is heated), `cap` (a TOU period's cap), `mode` (a mode change), `write` (a setpoint write) or `horizon` (the 24 hours run out first). Unknown when it won't heat within 24 hours, and when the heating stops short: any `stop` but `target`, except a setpoint write that ends it with the tank already at the new setpoint, which the library counts as reached |
| Simulated compressor start | When the heat pump's compressor next starts, as the walk finds it: the first heating interval whose source is the compressor. Its attributes: `running` (the heater reports the compressor running), and the start's `cause` and `trigger`. Unknown while the compressor runs (Simulated hot water ready is then when it stops), and when no start falls within 24 hours, such as in Electric or Vacation |
| Simulated energy, next 24 hours | kWh. A forecast, not a meter: it has no state class, so it stays out of long-term statistics and the Energy dashboard |
| Simulated hot water available | The hot water the tank can deliver now, in whole gallons or litres, as the library's `time_to_ready` says |
| Simulated showers ready | Whole showers the tank has hot water for now. Its `shower_gal` attribute is a shower's size |
| Simulated bath ready | On when the tank can fill a bath now. Its attributes: `bath_gal`, `margin_gal` (whole gallons), and `ready_at`, when the heat pump has it ready if not now |
| Simulated hot water for | What the hot water the tank can deliver now is enough for, in words: "2 baths", "1 bath and 1 refill", "1 bath", "3 showers", "1 shower" or "Less than a shower". The first bath is the library's bath answer; a refill is a top-up after the last whole bath from what is left; showers are counted when there is no bath. Its attributes: `baths`, `refill`, `showers`, and `bath_volume`, `refill_volume`, `shower_volume` in `volume_unit` (Home Assistant's) |
| Simulated bath ready at | When the tank holds a bath's hot water. Not ready now: when the heater on its own has it ready, unknown when not within 24 hours. Ready: since when, held while it stays ready (across a restart, and a walk with no answer), so a dashboard reads "10 minutes ago". Its `ready` attribute says which |

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

## The tank forecast card

The integration ships a Lovelace card, **NWP500 Tank Forecast**
(`custom:nwp500-tank-card`), loaded with the integration. It draws the
tank's upper and lower temperatures: as recorded over the last hours,
from the heater's Tank upper and Tank lower temperature sensors, and as
the simulator models them over the next day, dashed. The setpoint is
dotted, the modelled heating is shaded, and two markers say when the tank
comes to temperature and when the compressor next starts. Its heading
says the same in words: "Heat pump running · at temperature by 3:40 PM",
or "Heat pump off · next start 9:10 PM", and what the hot water is
enough for: "Hot water for 1 bath and 1 refill", or "Hot water for 2
showers · a bath by 7:10 PM".

```yaml
type: custom:nwp500-tank-card
entity: water_heater.heater
hours_back: 6     # recorded history shown, default 6
hours_ahead: 24   # modelled path shown, default 24
```

The tank sensors are found on the heater's device; `upper_entity` and
`lower_entity` name others. The card needs the simulator on for the
heater, and says so otherwise; it waits for a simulator that is still
starting, and keeps its subscription across Home Assistant's restarts. Recorded history needs the tank
sensors recorded (the recorder's default), and is drawn in Home
Assistant's temperature unit whatever a sensor's own unit. A heating a
TOU cap, a mode change or a setpoint write stops short is marked as
stopping short, not as at temperature.

### The path over the websocket API

The card reads the modelled path from two websocket commands, which any
frontend card or script can use too. Each takes the heater's `device_id`
or any of its entities' `entity_id`, and answers a user who can read the
heater's water heater entity (and the entity named):

- `nwp500/simulator/path` answers once.
- `nwp500/simulator/subscribe_path` sends the path now and again with
  every new simulation (at least every five minutes), keeps following the
  heater across a reload of the integration, and sends an empty path with
  `waiting: simulator_off` while the simulator is stopped or not yet
  started. It is taken for a heater whose integration hasn't loaded yet,
  so a frontend reconnecting while Home Assistant starts keeps it.

The path is in Home Assistant's temperature unit:

| Field | Meaning |
|---|---|
| `computed_at` | When the simulation started |
| `state` | As Simulated heating's |
| `compressor_running` | Whether the heater reports its compressor running |
| `heating_start`, `heating_end`, `stop`, `source` | The current or next heating: when it starts, when it ends (none when the 24 hours cut it), why it ends and what heats |
| `ready_at` | When the tank comes to temperature: `heating_end` for a heating that ends at its target (`stop` is `target`), none for one a TOU cap, a mode change or a setpoint write stops short |
| `compressor_start`, `compressor_trigger` | As Simulated compressor start's |
| `hot_water` | What the hot water is enough for, as Simulated hot water for: `summary`, `baths`, `refill`, `showers`; and `bath_ready` and `bath_ready_at` (when the heater on its own has a bath ready, none while it is ready) |
| `unit` | °F or °C |
| `points` | The tank along the next 24 hours at the library's points (about every 10 minutes): `at`, `upper`, `lower`, `setpoint` and `heating` |
| `waiting`, `error` | Why there is no path (`no_status`, `no_answer`, or `simulator_off` once the simulator stops), or that the simulation failed |

An ApexCharts card's `data_generator` can read it with
`hass.callWS({type: 'nwp500/simulator/path', entity_id: 'water_heater.heater'})`.

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

### From another integration

The actions are how another integration, script or automation models a
tank: they return their answers (`return_response`). With `use_heater`
off, the call needs no water heater: the scenario document is the whole
scenario, with its own `params` for any tank (without them, the library's
65 gal defaults), and nothing of the heater is read. The simulator must be
on for one of the integration's heaters, since its packages are installed
only then.

```python
response = await hass.services.async_call(
    "nwp500",
    "simulate",
    {"scenario": document, "use_heater": False, "include_points": True},
    blocking=True,
    return_response=True,
)
heating = response["result"]["heating"]
```

`get_scenario` gives a heater's scenario to start a document from, and
nwp500-python's `nwp500.sim.codec` writes one.

### The scenario

Every action takes:

| Field | Meaning |
|---|---|
| `device_id` or `entity_id` | The water heater. Not needed with `use_heater` off |
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
  volume in US gallons. Without them, the heater's own sizes (its Bath
  size and Shower size options), else the library's, which its
  `defaults_used` names.
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

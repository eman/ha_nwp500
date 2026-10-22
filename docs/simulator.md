# Hot water simulator

The hot water simulator models your water heater's tank from what the heater
reports. It answers three questions, kept up to date as sensors:

- Is the tank heating now, or will it heat within the next 24 hours?
- When will the tank next be fully heated?
- How much electricity is the heater likely to use over the next 24 hours?

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

Turning the simulator off removes its sensors and the little it stores.
A [recorded history](#recording-the-heaters-history) is kept unless you
choose to delete it on the same page.

## Inputs

For each water heater you can choose two sensors and a weather entity. All
are optional:

| Input | Used for | Without it |
|---|---|---|
| Air temperature beside the heater | How fast the heat pump heats: it takes its heat from this air | The heater's own reading while the heat pump runs; otherwise 65 °F (18 °C), and `reasons` says `ambient_defaulted` |
| Humidity beside the heater | The heat pump's output | 50% |
| Weather forecast | The air over the next hours, [projected from the forecast](#the-air-ahead-from-a-weather-forecast) | The air sensor's reading, held |
| Water meter, its flow rate, the cold supply's temperature, a circulation pump's speed | [How much hot water the tank holds](#hot-water-use) | No hot water sensors |
| Bath and shower sizes | Whether a bath is ready, and how many showers | 28.7 gallons (109 L) and 12 gallons (45 L) |

A sensor that has not reported for an hour is not used. One that keeps
reporting the same value is.

Don't choose the heater's own ambient temperature sensor as the air sensor.
While the heat pump is off, it reads the cabinet, several degrees warm. The
simulator uses that reading only while the heat pump runs, when the fan
draws the room's air past it.

The simulator also reads, from the integration itself, the tank's
temperatures, the mode and setpoint, the heater's reservation schedule and
its time-of-use schedule.

## The air ahead, from a weather forecast

The air beside the heater changes over a day, with the weather, and the
heat pump heats slower in colder air. With an air sensor alone, the
simulator holds the air at its reading for the whole day ahead. With a
weather entity too, it looks ahead.

Choose a weather entity with an hourly forecast. Each
[calibration](#fitting-it-to-your-heater) then fits how the air beside the
heater follows the outdoor air: how much of the outdoor swing reaches it,
how late, and the offset. It fits on the recorder's history of the air
sensor and of the weather entity's temperature, at most 60 days, averaged
per half hour. A half hour counts only where the outdoor air is known over
the 6 hours before it, with no gap over 3 hours between its readings; it
needs at least 72 of them, over at least 3 days. The simulator then
projects the air over the day ahead from the hourly forecast, moved to pass
through the air sensor's reading now.

As with the heater's terms, a later run replaces the model only when it
does better: the latest fifth of the readings is held out of the refit,
and the model whose error spreads less there is kept. A model is kept with
the air sensor and weather entity it was fitted to: choosing another drops
it until the next fit. Where the air barely
follows the outdoor air (a correlation under 0.5, as in a heated room), no
model is fitted, and the air is held at its reading as before.

The projection needs the air sensor, to fit it and to anchor it: without
one, or while its reading is unavailable or more than an hour old, the
forecast is not used. The `location_air` attribute of the
**Simulator calibration** sensor says what the last run found: the model
in force (`damping`, `lag_hours`, `offset_f`), whether the heater's model
(`base`) or the refit was kept, the correlation `r`, the half-hours fitted,
each model's error on the readings held out, °F, and `why` it kept what it
kept.

## Hot water use

With a water meter, the simulator says how much hot water the tank holds:
the gallons it can deliver now, how many showers, and whether a bath is
ready. It books the tank's heat: from the last time the tank stood full
(quiet after heating, both probes within 1.5 °F), it takes out the heat
each draw carries and what the tank loses standing, and adds what the
compressor and the elements put in.

**The inputs.** All are sensors, chosen per heater:

- **Water meter**, needed: a meter of the water the heater supplies, on
  its cold inlet or its hot outlet. Every gallon it reads is booked as hot
  water drawn, so a meter of the whole house's water, which also reads
  toilets, the garden and the cold taps, would empty the tank on paper.
  Either the volume each report adds, or a running
  total (a sensor whose state class is `total` or `total_increasing`);
  a reset of a running total is counted from its new value. It has to
  report at rest too: a meter silent for over an hour leaves no book.
- **Water meter flow rate**, optional: finds short draws the volume
  alone misses.
- **Cold supply temperature**, optional: a probe on the cold pipe into the
  heater. At rest it reads the air around the pipe, so only the last two
  minutes of each long draw (at least 11 minutes, no circulation) count:
  their median over the last 72 hours is the mains temperature. Without
  it, the gallons are counted at a modelled inlet, and `reasons` says
  `inlet_temp_modelled`.
- **Circulation pump speed**, optional: a hot water circulation pump's
  speed, in rpm. Its runs move water the meter may read and take heat from
  the tank; a long draw during one isn't read for the mains. A pump with
  no reading in force is unknown, and there's no book until it reads
  again. Until the loop's terms are fitted (below), a run since the tank
  was last full leaves no book: the run's flow would be booked as draws
  and its loss not at all.
- **Bath and shower sizes**: the hot water each takes.

**Fitted to your heater.** With a meter, each calibration fits the book's
own terms from the last 42 days of the meter and the heater's history:
the heat pump's heating rate and how it changes with the air, and the
tank's standby loss. With a pump, it fits the loop's terms with them: the
flow the meter reads of a run, and the heat a run takes from the tank.
They are fitted between the times the tank stood full, a pair of them
with no heating in between; the loop's need ten pairs with a pump run in
between, which a pump running about once a day gives in about four
weeks. The latest fifth of the pairs is held out, and a fit replaces the
terms in force only when it books those no worse. The terms are kept for
this meter and pump, apart from the heater's own fit, and fitted afresh
for another meter or pump. The calibration report's `book` says what was
fitted and why.

The fit reads what Home Assistant's recorder keeps, from the pump's first
recorded speed. The recorder keeps 10 days unless told otherwise, which is
seldom enough for the loop's terms: set its `purge_keep_days` to 42 or
more for them to be fitted.

**Where it comes from.** Every 10 minutes the simulator reads the last 72
hours from Home Assistant's recorder: the meter, the pump and the probe,
and the heater's own sensors (as [calibration](#fitting-it-to-your-heater)
reads them: the Last MQTT response and tank temperature sensors must be
recorded; the compressor and elements are used when enabled). A book
needs a full tank in those 72 hours with the heater heard from since. On
an account with several heaters there is none, as for calibration.

**The sensors**, for a heater with a meter:

| Sensor | State |
|---|---|
| Simulated hot water available | The hot water the tank can deliver now, at 105 °F, in your volume unit |
| Simulated showers ready | Whole showers the tank can deliver now; the attribute `showers` has the fraction, `shower_gal` the size |
| Simulated bath ready | On when the tank can fill a bath now. `margin_gal` is what it holds beyond a bath (below 0: short of it), `ready_at` when the heat pump has it ready if not now, `bath_gal` the size |

They are unavailable while there is no book; `notes` says why (no full
tank to count from, a silent meter, history not recorded). `inlet_measured`
says whether the gallons are counted at the probe's mains. Diagnostics
show the book: when it was read, its heat, how long since the tank stood
full, and the mains.

## Recording the heater's history

Each heater's form also offers **Record the heater's history**, off unless
you turn it on. The recording is the finest history the simulator can be
[fitted](#fitting-it-to-your-heater) to.

- **What is kept.** Each status the heater reports, as it reported it, and
  when it arrived. The heater's address is left out.
- **Where.** In Home Assistant's configuration folder, under
  `nwp500/history/`, one folder per heater and one file per day (UTC). The
  files go into Home Assistant's backups.
- **When it is written.** Every 15 minutes, to spare SD cards, and when the
  integration reloads or Home Assistant stops.
- **How big.** The day in progress is plain text, about 20 MB by its end
  for a heater reporting every 10 seconds or so. Once the day is over, its
  file is compressed, to about 0.2 MB.
- **How long.** **Days of history to keep**, 60 unless you change it
  (7 to 365). Older days are deleted.

Compressing and deleting old days happen while the heater records. A
history no longer recorded, because its recording or the simulator was
turned off, stays as it was left until you delete it.
- **How often.** Every status the heater sends, which is more often than
  the **Scan interval** option asks for: the heater answers each request,
  sometimes twice, and sends others between them. One heater sent about
  one every 10 seconds, many of them unchanged from the one before.

Nothing is filled in: a stretch with no file, or no lines, is a time
nothing arrived, such as an outage or a restart.

Turning the recording off stops it and keeps what was recorded. To delete
it, tick **Delete the history recorded so far** on the heater's form, or
**Delete the recorded heater history** when you turn the simulator off.
Removing the integration deletes it too. The diagnostics show, per heater,
how many days are stored and their size, when the last status arrived, and
the longest wait between two since the integration started.

## Fitting it to your heater

Once a week, at 03:30, the simulator refits its terms to each heater from
the heater's history, and again whenever you press the heater's
**Calibrate simulator** button. One heater is fitted at a time, in the
background; on a small machine, weeks of history take minutes.

**Where the history comes from.** One of two, whichever reaches further
back:

- the [recorded history](#recording-the-heaters-history), each status as
  the heater reported it;
- Home Assistant's recorder, which keeps the changes of the heater's own
  sensors, 10 days unless you set its `purge_keep_days` (at most 60 days
  are read). It needs the **Last MQTT response**, **Tank upper temperature**
  and **Tank lower temperature** sensors enabled and recorded. More are used when they are
  enabled: the compressor and elements, heat source, DHW in use, operation
  mode, error code, current power, energy to setpoint, the heat pump's
  lower off temperature setting, the ambient temperature, and
  **Anti-Legionella cycle running**, without which those cycles are not
  left out of the fit.

The recording is the finer of the two, and is used once it reaches as far
back as the recorder's history of the tank; if one fails, the other is
fitted. On an account with several heaters only the recording is used:
the Last MQTT response sensor is the account's, so the recorder's history
can't say when each heater was heard from. The air and humidity sensors' history comes from the
recorder.

**The air matters.** Without an air sensor, the standby loss is not fitted.
The heat pump's terms need the air too: the air sensor's, or else the
heater's own **Ambient temperature** reading while the compressor runs,
which the recorded statuses carry but the entities' history has only when
that sensor is enabled (it is off by default). With neither, no recovery
can be fitted.

**What changes.** A run refits what the heater runs on: its last fit, or
the defaults for its tank's size. A term this history can't fit keeps its
value. A first fit replaces the defaults, and the simulator walks the
heater with it from then on, across restarts. A later run refines the
heater's fit without making it worse. Each group of terms (the standby
loss, the gap between the zones, the compressor, input power, COP) is
refitted with its latest fifth of evidence held out, and replaces the fit
only if it scores better on that evidence. A group with fewer than 10
windows, recoveries or cycles keeps the fit, so a fit on a long history
is not worn down by the recorder's few days. A run that fits nothing, or
fails, leaves the heater as it was.

**The Simulator calibration sensor**, under the heater's diagnostics, is
when the simulator was last fitted, and unknown while it runs on the
defaults. Its attributes describe the last run:

- `source`: `statuses` or `entities`;
- `applied`: whether the run changed the heater's parameters;
- `judged`, when refining: each group, whether the fit (`base`) or the
  refit was kept, both errors on the evidence held out, and why;
- `terms`: each term's `value`, what it was fitted `from`, its `error`,
  whether it was `fitted`, and `why` not;
- `statuses` (the statuses or changes read), `gaps`, `air` (how the air
  was given), `duration_s`, `error`, `notes` and `running`.

`location_air`, with a weather entity, is the air's model: see
[the air ahead](#the-air-ahead-from-a-weather-forecast).

`terms`, `judged`, `location_air` and `notes` are not recorded in history. Three runs in a row that
fit nothing raise a Repairs issue, cleared by the next fit.

A fit needs weeks of history with quiet nights, when no water is drawn,
and full heat pump recoveries.

## Sensors

| Sensor | State |
|---|---|
| Simulated heating | `heating`, `scheduled` (will heat within 24 hours), `idle` (won't), or `disabled` (Vacation, powered off or Standby) |
| Simulated hot water ready | When the tank will next be fully heated: the end of the heating it is in, or of the next one, including heating that goes straight on in another mode or zone. Unknown when it won't heat, or won't finish heating, within 24 hours, or when a mode change cuts it short |
| Simulated energy, next 24 hours | kWh. A forecast, not a meter: it has no state class, so it stays out of long-term statistics and the Energy dashboard |

Each sensor has these attributes:

- `model` is the engine the answer comes from: `physics`.
- `reasons` lists why the answer may be less than it seems, such as `no_heat_book`, `ambient_defaulted` or `reservations_unknown`.
- `defaults_used` lists each default the answer used, as `name=value`.
- `notes` says what the air and humidity rest on.

Simulated heating also has the next heating interval's start (`starts_at`, none while it is heating), its `source` (compressor or element), `cause`, `trigger`, `energy_kwh`, `tou_capped` and `confidence`. Energies are to a tenth of a kWh, so an answer that hasn't changed adds nothing to history.

`reasons`, `defaults_used` and `notes` are not recorded in history.

The sensors appear once the simulator is running, which can take a few
minutes while its packages install. They are unavailable while the heater
isn't reporting, before its details have arrived, and when a simulation
fails (the log says why, once). In Standby nothing heats: Simulated heating
says `disabled` and the energy is 0.

The simulation runs again when the heater reports, at most once a minute,
and every five minutes in any case.

## The `nwp500.simulate` action

`nwp500.simulate` returns a simulation from now and changes nothing on the
heater. It needs the simulator running, and refuses to answer while the
heater is in Standby.

| Field | Meaning |
|---|---|
| `device_id` or `entity_id` | The water heater |
| `hours` | How far ahead, 1 to 168. Default 24 |
| `writes` | Setpoint or mode changes to try. Each has `at` (when, within the hours simulated), and `temperature` (in Home Assistant's unit, 104.9-149.9 °F or 40.5-65.5 °C), `mode` (`heat_pump`, `electric`, `energy_saver` or `high_demand`), or both. A change into Vacation or Power Off isn't modelled |
| `include_points` | Also return the tank's path. Default off |
| `resolution_minutes` | With the path, keep one point every this many minutes, 1 to 60 |

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

The response is the library's result document, in its units (°F, Wh, US
gallons, named under `units`). Its fields:

- `heating`: each heating interval, with its start and cause, end, source, target and energy.
- `writes`: setpoint writes and reservation firings, as they would land.
- `mode_writes`: mode writes, as they would land.
- `modes`: the mode over time.
- `points`: the tank's path, only when asked for. Unthinned, it comes with
  `delivery`, the delivered water's temperature at each point.
- `summary`: the same answer the sensors give, over the hours asked for:
  `state`, `starts_at`, `ready_at`, `source`, `energy_kwh` (the next
  heating) and `energy_total_kwh`.
- `reasons`, `defaults_used` and `notes`.

A time without a time zone is taken in Home Assistant's.

## Accuracy

These answers come from physics, with the default parameters for the
tank's size until the simulator is [fitted](#fitting-it-to-your-heater) to
the heater. Expect:

- **Long recoveries run short.** Physics alone says a long recovery ends
  sooner than it does. At one installation, recoveries with more than three
  hours to go ended about three quarters of an hour later than physics said.
- **The air matters most.** Without an air sensor, the answer rests on a
  default air temperature whenever the heat pump is off.
- **Hot water use is booked, not forecast.** With a meter, the simulator
  knows the water drawn so far and the heat left; it doesn't yet expect
  the draws ahead, so the heating it plans is for a quiet tank.

## Planned

These are planned, in this order:

1. Services a scheduler can plan with.

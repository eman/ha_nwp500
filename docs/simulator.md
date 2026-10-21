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

For each water heater you can choose two sensors. Both are optional:

| Input | Used for | Without it |
|---|---|---|
| Air temperature beside the heater | How fast the heat pump heats: it takes its heat from this air | The heater's own reading while the heat pump runs; otherwise 65 °F (18 °C), and `reasons` says `ambient_defaulted` |
| Humidity beside the heater | The heat pump's output | 50% |

A sensor that has not reported for an hour is not used. One that keeps
reporting the same value is.

Don't choose the heater's own ambient temperature sensor as the air sensor.
While the heat pump is off, it reads the cabinet, several degrees warm. The
simulator uses that reading only while the heat pump runs, when the fan
draws the room's air past it.

The simulator also reads, from the integration itself, the tank's
temperatures, the mode and setpoint, the heater's reservation schedule and
its time-of-use schedule.

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
back as the recorder. The air and humidity sensors' history comes from the
recorder.

**The air matters.** Without an air sensor, the standby loss is not fitted.
The heat pump's terms need the air too: the air sensor's, or else the
heater's own **Ambient temperature** reading while the compressor runs,
which the recorded statuses carry but the entities' history has only when
that sensor is enabled (it is off by default). With neither, no recovery
can be fitted.

**What changes.** A run refits what the heater runs on: its last fit, or
the defaults for its tank's size. A term this history can't fit keeps its
value. The simulator walks the heater with the result from then on, and
keeps it across restarts. A run that fits nothing, or fails, leaves the
heater as it was.

**The Simulator calibration sensor**, under the heater's diagnostics, is
when the simulator was last fitted, and unknown while it runs on the
defaults. Its attributes describe the last run:

- `source`: `statuses`, `entities`, or `loaded` (see below);
- `terms`: each term's `value`, what it was fitted `from`, its `error`,
  whether it was `fitted`, and `why` not;
- `statuses` (the statuses or changes read), `gaps`, `air` (how the air
  was given), `duration_s`, `error`, `notes` and `running`.

`terms` and `notes` are not recorded in history. Three runs in a row that
fit nothing raise a Repairs issue, cleared by the next fit.

A fit needs weeks of history with quiet nights, when no water is drawn,
and full heat pump recoveries.

**A fit made elsewhere.** `nwp500.set_simulator_calibration` gives a heater
parameters fitted outside Home Assistant, such as from a longer history
kept elsewhere, as nwp500-python's calibration writes them
(`params_to_document`), with an optional `fitted_at`. The simulator walks
with them from then on. The weekly run leaves them be, since they come
from a longer history than Home Assistant keeps; pressing **Calibrate
simulator** refits them on Home Assistant's own history. The sensor's
`loaded` attribute says which.
In **Developer tools → Actions**, paste the document as `parameters`:

```yaml
action: nwp500.set_simulator_calibration
data:
  device_id: <your water heater's device>
  parameters: {"schema": "heater-params/1", "tank": {...}, ...}
```

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
- **No hot water use is modelled yet.** The simulator doesn't know when
  water is drawn, so it can't say how many showers the tank holds.

## Planned

These are planned, in this order:

1. The air beside the heater from a weather forecast, fitted to the air
   sensor's history.
2. Hot water use, from a water meter and an inlet temperature probe:
   deliverable volume, showers and bath readiness.
3. Services a scheduler can plan with.

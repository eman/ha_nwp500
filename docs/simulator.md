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

Turning the simulator off removes its sensors. The simulator stores
nothing of its own.

## Inputs

For each water heater you can choose two sensors. Both are optional:

| Input | Used for | Without it |
|---|---|---|
| Air temperature beside the heater | How fast the heat pump heats: it takes its heat from this air | The heater's own reading of the air at its intake |
| Humidity beside the heater | The heat pump's output | 50% |

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

Each sensor has these attributes:

- `reasons` lists why the answer may be less than it seems, such as `no_heat_book`, `ambient_defaulted` or `reservations_unknown`.
- `defaults_used` lists each default the answer used, as `name=value`.
- `notes` says what the air and humidity rest on.

Simulated heating also has the next heating interval's start (`starts_at`, none while it is heating), its `source` (compressor or element), `cause`, `trigger`, `energy_kwh`, `tou_capped` and `confidence`. Energies are to a tenth of a kWh, so an answer that hasn't changed adds nothing to history.

`reasons`, `defaults_used` and `notes` are not recorded in history.

The sensors appear once the simulator is running, which can take a few
minutes while its packages install. They are unavailable while the heater
isn't reporting, before its details have arrived, and when a simulation
fails (the log says why, once). They are also unavailable in Standby, which
the library doesn't model.

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

The response is the library's result document (`nwp500.sim.codec`),
unchanged, in its units: °F, Wh and US gallons. Its fields:

- `heating`: each heating interval, with its start and cause, end, source, target and energy.
- `writes`: setpoint writes and reservation firings, as they would land.
- `mode_writes`: mode writes, as they would land.
- `modes`: the mode over time.
- `points` and `delivery`: the tank's path and the delivered water's
  temperature at each point, only when asked for.
- `reasons` and `defaults_used`, the library's own.

Beside them, `inputs` holds the integration's notes on what it gave the
library: `reasons` (such as `reservations_unknown`, when the heater's
reservation schedule hasn't been read yet) and `notes` (what the air and
humidity rest on).

A setpoint the library can't run is refused with its own message.

A time without a time zone is taken in Home Assistant's.

## Accuracy

These answers come from physics with the default parameters for the tank's
size. Expect:

- **Long recoveries run short.** Physics alone says a long recovery ends
  sooner than it does. At one installation, recoveries with more than three
  hours to go ended about three quarters of an hour later than physics said.
- **The air matters most.** Without an air sensor, the answer rests on the
  heater's own reading, which runs warm while the heat pump is off.
- **No hot water use is modelled yet.** The simulator doesn't know when
  water is drawn, so it can't say how many showers the tank holds.

## Planned

These are planned, in this order:

1. Hot water use, from sensors of the water drawn and of the inlet
   temperature: deliverable volume, showers and bath readiness.
2. Services a scheduler can plan with.

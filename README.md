# Midea Air Control

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A custom [Home Assistant](https://www.home-assistant.io/) integration for air
conditioning systems (typically VRFs) controlled by the **M-control** mobile app
or the **AirControlBase** web portal (`aircontrolbase.com`). Each indoor unit is
exposed as a Home Assistant `climate` entity.

## Features

- Climate control for every indoor unit reported by the controller: power, HVAC
  mode (cool/heat/dry/fan/auto), target temperature, fan speed, and swing.
- Single cloud poll feeds all indoor units (one request per update cycle).
- **Instant UI feedback.** Commands are applied optimistically and held until
  the cloud reflects them, so the card doesn't flicker back during the few
  seconds the cloud takes to update.
- **Remembers your settings while off.** The cloud forgets a unit's mode/target
  temperature/fan while it is off (and reports defaults). This integration
  remembers the last powered-on settings, restores them on turn-on, shows them
  while off, and persists them across Home Assistant restarts.
- Automatic session renewal when the cloud login cookie expires.

## Installation

### HACS (recommended)

1. In HACS, go to **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/mbb842/midea_air_control` with category
   **Integration**.
3. Search for **Midea Air Control**, install it, and restart Home Assistant.

### Manual

Copy `custom_components/midea_air_control` into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Requirements

- An air-conditioning system you can already control through the **M-control**
  app or the **AirControlBase** web portal.
- The email and password you use to sign in to that app / portal.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration →
Midea Air Control**, then enter the email and password you use for the M-control
app / AirControlBase portal. All indoor units on the account are added
automatically.

## Debugging

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.midea_air_control: debug
```

Log levels used by the integration:

- **DEBUG** — every cloud poll and its result.
- **INFO** — setup, every user command, and credential renewals.
- **WARNING** — a device dropping offline, or a command the cloud accepted but
  the device never reflected.
- **ERROR** — login/communication failures.

## Disclaimer

This is an unofficial integration and is not affiliated with or endorsed by
Midea. It has only been tested on an air-conditioning system using the **CCM15**
controller; other controllers exposed through M-control / AirControlBase may or
may not work. Use at your own risk.

## License

[MIT](LICENSE)

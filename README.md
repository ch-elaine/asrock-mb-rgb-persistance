# diy-rgb-tools

Small Linux tools for RGB hardware that OpenRGB doesn't fully handle.

- `rgbtool` saves an OpenRGB profile into the motherboard's and the RAM's own memory, so the
  lighting survives a reboot without OpenRGB running. `rgbtool keyboard` runs `ak820pro.py`.
- `ak820pro.py` sets a static colour on the Ajazz AK820 Pro keyboard, shows a GIF, PNG or JPEG
  on its screen (resized to 128×128), and syncs its clock.
- `trcc-daemon.service` starts TRCC, which drives the Thermalright cooler's screen.

## Tested on

- ASRock B650M Pro RS, Polychrome USB controller (`26ce:01a2`)
- RAM with ENE RGB controllers (`AUDA0-E6K5-0101`)
- Ajazz AK820 Pro, wired, firmware 1.14 (`0c45:8009`)
- Thermalright cooler display (`0416:8001`), through TRCC

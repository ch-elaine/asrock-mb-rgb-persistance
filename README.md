# asrock-rgb-persist

Saves an OpenRGB profile into the hardware, so the lighting survives reboots, the way
Polychrome Sync does on Windows. It covers:

- **The ASRock board:** the Polychrome USB controller (`26ce:01a2`, e.g. B650M Pro RS).
  OpenRGB never sends its commit-to-flash command (`0x12`). This script converts the
  profile to the controller's built-in per-zone effects, writes them, and commits them.
- **ENE RAM sticks** ("ENE DRAM" in OpenRGB). OpenRGB's command line programs the
  profile into the sticks. The script then sends each stick the save command that
  OpenRGB's hidden "Save To Device" button sends.

It needs only Python 3 and OpenRGB, and runs as your normal user. OpenRGB's udev rule
and the `i2c` group already give you access.

## Use

Save the profile in OpenRGB and **close OpenRGB**, then run:

```sh
./asrock-rgb-persist apply -n             # show what would be written
./asrock-rgb-persist apply                # save to board + RAM
./asrock-rgb-persist apply --profile Work # a specific profile
./asrock-rgb-persist status               # what the board and RAM hold now
```

The default profile is OpenRGB's start-up profile (Settings → Profiles); if none is set, the
newest one is used.

OpenRGB must be closed for the RAM part, because both talk to the RAM over the same bus.
If OpenRGB is open, the board is still saved, the RAM is left alone, and the script says so.

## How the RAM part stays safe

The RAM shares its bus with each stick's memory-config chip and the power chip that sets
memory voltage. So the script:

- only ever addresses `0x70`–`0x77`, OpenRGB's ENE RAM range;
- checks each stick with OpenRGB's read-only ENE signature, plus a known ENE RAM name;
- saves only when the stick holds the profile's mode.

## If the board is dark again after a reboot

OpenRGB re-applies a profile when it exits. If that profile puts the board in Direct
mode, the exit write may replace what this script saved. Use Static (or another
effect) for the board in the profile, or just run `apply` again.

## Limits

- The board stores one effect and one colour per zone. Direct mode is saved as Static,
  using each zone's first LED colour.
- RAM in Direct mode can't be saved to the stick; use Static or another effect.
- Only OpenRGB 1.x JSON profiles are supported, not the older `.orp` format.

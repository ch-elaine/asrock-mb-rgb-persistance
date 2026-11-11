# asrock-rgb-persist

Makes OpenRGB lighting on ASRock Polychrome USB boards (`26ce:01a2`, e.g. B650M Pro RS)
survive reboots, the way Polychrome Sync does on Windows.

OpenRGB never sends the controller's commit-to-flash command (`0x12`). Its Direct mode is
also a live stream that the controller can't store. So after power-on the LEDs stay dark until
OpenRGB starts. This script reads a saved OpenRGB profile, turns the ASRock entry into the
controller's built-in per-zone effects, writes them, and commits them to flash.

It needs only Python 3 and runs as your normal user; OpenRGB's udev rule already gives you
access to the controller.

## Use

Save the profile in OpenRGB, then run:

```sh
./asrock-rgb-persist apply -n             # show what would be written
./asrock-rgb-persist apply                # write + commit
./asrock-rgb-persist apply --profile Work # a specific profile
./asrock-rgb-persist status               # what the controller holds now
```

The default profile is OpenRGB's start-up profile (Settings → Profiles); if none is set, the
newest one is used. Changes count only once they're **saved** to the profile in OpenRGB.

## If the LEDs are dark again after a reboot

OpenRGB re-applies a profile when it exits (Settings → Profiles → exit, suspend and shutdown
profiles). If that profile is in Direct mode, OpenRGB's exit write may replace what this script
saved. Fix it either way:

- Save the profile in **Static** mode instead of Direct. A single colour per zone looks the same.
- Or turn off OpenRGB's exit and shutdown profiles.

## Limits

- The controller stores one effect and one colour per zone. A Direct-mode profile is saved
  as Static, using each zone's first LED colour. Per-LED gradients need OpenRGB running.
- Only OpenRGB 1.x JSON profiles are supported, not the older `.orp` format.

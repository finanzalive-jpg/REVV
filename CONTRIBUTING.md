# Contributing to Rise

Thanks for helping. Rise exists to keep older computers useful, so the most valuable contribution is simple: **try it on real hardware and tell us what happened.**

## Test on your hardware

1. Write the latest ISO from [Releases](../../releases/latest) to a USB stick and boot from it.
2. Open an issue with:
   - PC or laptop model and year
   - processor, RAM, graphics card (Rise shows them in *Impostazioni → Sistema e hardware*)
   - what worked and what did not: Wi‑Fi, sound, brightness, screen, USB, battery
   - time from the boot menu to the desktop

Reports from machines made between 2010 and 2015 are especially welcome.

## Change the desktop

The whole desktop is `desktop/index.html`. Open it in a browser to work on it: network, files and hardware are simulated there. Keep it light: no frameworks, no extra network requests.

## Change the system

- `system/bin/rise-shell` is the bridge between the desktop and Linux. Each command is a small Python function in `CMDS`.
- `build/packages.txt` lists every package in the image. Every package added costs memory and disk on old PCs; explain why it is needed.
- `build/build-iso.sh` builds the ISO. Run it with `sudo` on Ubuntu 24.04 or newer.

## Pull requests

- One topic per pull request.
- Describe what you tested and on which hardware.
- Interface text is in plain words: say what a control does, not how it works inside.

By contributing you agree that your work is released under the GPL-3.0 license of the project.

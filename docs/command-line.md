# Command line

`winlight-cli.exe` sits next to `Winlight.exe` in the installed folder (`%LOCALAPPDATA%\Programs\Winlight`). The installer's "Add winlight-cli to PATH" option lets any terminal find it by name. It talks to the running app, so Winlight must be running.

```powershell
winlight-cli --list                      # displays, their numbers, ids and brightness
winlight-cli --set 40                    # every display to 40% (hidden ones excepted)
winlight-cli --set +10 --display 2       # display 2 up by 10
winlight-cli --set 70 --display DELL     # by name (any part, custom names too) or id (or part of one)
winlight-cli --set +10 --panel           # and show the panel, for hotkey tools
winlight-cli --get --display 1           # prints just the number
winlight-cli --night-light toggle        # also on, off, suspend, resume, auto (follow the schedule), status
winlight-cli --calibration reload        # reload each display's calibration from its color profile
winlight-cli --refresh                   # detect displays again
winlight-cli --list --json               # for scripts
winlight-cli --diagnostics               # save a Report a Problem zip and print its path
```

| Option | Does |
|---|---|
| `--list` | Lists the displays with their numbers, ids and brightness |
| `--get` | Prints the brightness of `--display` (all by default) |
| `--set VALUE` | Sets brightness: `0`–`100`, or `+N`/`-N` to change it |
| `--display WHICH` | A number from `--list`, an id or part of one, part of a name, or `all` (the default) |
| `--night-light ACTION` | `on` or `off` until changed back, `toggle`, `suspend` (until resumed, as the panel's Suspend button), `resume`, `auto` (follow the schedule), `status` |
| `--calibration ACTION` | `status` (each display's color calibration), `reload` (from each display's color profile, as Windows does at sign-in), `forget` (the calibrations remembered for putting back) |
| `--refresh` | Detects displays again |
| `--panel` | Shows Winlight's panel, after any change asked for |
| `--json` | Prints JSON instead of text |
| `--diagnostics` | Saves the same zip as Report a Problem and prints its path |

- Display numbers are the order in the panel and in `--list`. `all` leaves hidden displays alone.
- Changes fade rather than jump; a laptop panel eases changes itself.
- **Exit codes:** `0` done; `1` the request failed or the command has a mistake (the message says why); `2` Winlight isn't running.
- Output to a file or a pipe is UTF-8, so display names in any script survive redirection.
- Run without options, `winlight-cli` prints its help.

`winlight-cli.exe` is a console program in its own right, beside the windowed `Winlight.exe`, so a terminal waits for it and receives its output and exit code; it needs no shim or wrapper script.

## Not included

Contrast, input switching and raw DDC/CI codes are left out on purpose: an arbitrary DDC/CI write can switch a monitor's input, turn it off or reset it, and these need testing on many monitors first.

## Coming from another tool

Twinkle Tray's command line is included with the app. Monitorian's is one of the add-on features of its Microsoft Store version. PowerToys Power Display ships `PowerToys.PowerDisplay.Cli.exe`.

| Twinkle Tray | Monitorian (add-on) | PowerToys Power Display | Winlight |
|---|---|---|---|
| `--List` | `/get all` | `list` | `--list` (`--json` for scripts) |
| `--MonitorNum=1 --Set=40` | `/set <id> 40` | `set --monitor-number 1 --brightness 40` | `--set 40 --display 1` |
| `--Offset=10` | `/set <id> +10` | `up --brightness --step 10` | `--set +10` |
| `--MonitorID=<part of id>` | `<id>` | `--monitor-id <id>` | `--display <id, or part of it, or of a name>` |
| `--All` | `all` | `--monitor-number 1,2,…` | the default, or `--display all` |
| `--Panel` | | | `--panel` |
| `--VCP` | contrast, input, color preset | contrast, volume, input, color temperature, power | not included (see above) |

Sources: [Twinkle Tray's README](https://github.com/xanderfrangos/twinkle-tray), [Monitorian's README](https://github.com/emoacht/Monitorian), [PowerToys Power Display](https://learn.microsoft.com/windows/powertoys/power-display).

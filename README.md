# System Monitor

<img src="docs/icon.png" width="96" align="right" alt="">

CPU, GPU and RAM usage and temperatures for Windows 10/11, with history charts, the busiest processes, and an
**alarm** when the CPU or GPU gets too hot — with actions: end the busiest process, sleep, shut down (or do it
automatically after a countdown), a cool-down mode, process rules and phone alerts.

A stand-alone app: one Python file, its own icon, and its own settings (`system_monitor.ini`), all in this
folder. [Window Manager](../README.md) can open it (type `system monitor` in its search bar), but doesn't need
to be there.

## Requirements

- Windows 10 or 11
- Python 3.8+
- [`psutil`](https://pypi.org/project/psutil/)

```bash
python -m pip install -r requirements.txt
```

## Run

```bash
python system_monitor.py              # --minimized starts it in the taskbar
```

Build `SystemMonitor.exe` (with its icon) into `dist\`: double-click **`build.bat`**, or

```bash
pip install pyinstaller psutil
python system_monitor.py icon system_monitor.ico
pyinstaller --onefile --windowed --icon system_monitor.ico --name SystemMonitor system_monitor.py
```

Copy `system_monitor.ini` next to `SystemMonitor.exe` to keep your settings. Only one System Monitor runs at a
time; starting it again does nothing.

## Features

- **CPU / GPU / Memory cards** — usage, temperature (green → orange at *warning* → red at *critical*), clock,
  power, VRAM, hot spot, fan speed, and a 2-minute chart (blue = usage, orange = temperature, dashed =
  warning). **Click a chart** to switch it to **power (W, yellow) and fan speed (green)**.
- **Overlay** — a small strip always on top in the bottom-right corner: `CPU 23% 64°C │ GPU 45% 70°C`
  (temperatures green / orange / red, the border turns orange or red during an alarm). It stays while the
  monitor runs, even minimized, and never takes the focus. **Double-click** opens System Monitor; **drag** it
  anywhere; **right-click** → *Move to* another corner, *Back to its corner*, *Hide*. Settings in `[overlay]`.
- **Processes** — sorted by CPU (click a header to sort by name, PID, **GPU** or memory — the GPU column shows
  which app uses the graphics card, like Task Manager), filter box, **End process**,
  **End process tree** (with everything it started), right-click → *Open file location*. Windows' own
  processes (`[processes] protected`) can't be ended — that would crash Windows.
- **Buttons** — *Shut down…*, *Restart…*, *Sleep*, *Shut down later ▾* (in N minutes / cancel), *History*,
  *Test alarm* (also sends a test to your phone when Telegram is set up).
- **Alarm** — when a temperature stays above its *warning* for `hold_seconds`, a pop-up appears in the
  bottom-right corner (it doesn't take the focus from a game or video) with a sound, the busiest processes
  and buttons: **End** a process, **Sleep**, **Shut down in 60 s**, **Snooze 10 min**, **Dismiss**. Above
  *critical* the sound repeats. It closes by itself once things are back to normal.
- **Automatic action** — `[critical] action = shutdown | sleep | kill_top` does it by itself when a
  temperature is critical, after `countdown_seconds` (the pop-up shows the countdown with **Cancel**; it's
  also cancelled if the temperature drops). Default `ask` = only the pop-up.
- **Cool-down mode** — when the CPU or GPU reaches its *warning* temperature, System Monitor lowers Windows'
  *maximum processor state* to 80 % (`[cooldown]`): the CPU stops boosting, makes much less heat, and is only
  a little slower. Once the temperature has stayed 5 °C below the warning for 3 minutes, it's put back
  exactly as it was. While it's on, the CPU card and the overlay show ❄. If the monitor is closed or crashes
  during a cool-down, the setting is put back the next time it starts. `method = power_saver` switches to
  Windows' *Power saver* plan instead.
- **History** — every 10 minutes the lowest / average / highest CPU and GPU temperature (and usage, power) is
  added to `system_monitor_history.csv` (kept `keep_days`, 90 by default). The **History** button shows the last
  24 hours, 7 days or 30 days, with the highest temperature and when it happened — handy to see if the PC
  runs hotter than before (dust in the fans, dried thermal paste). Move the mouse over the chart for details.
- **Process rules** (`[rules]`) — e.g. `* cpu > 90 for 5 min -> warn`: when a program stays above the
  limit that long, a *Process alert* pop-up shows it with **End** / **Ignore**; `-> end` ends it by itself,
  `-> notify` only sends it to your phone. See [Process rules](#process-rules).
- **Phone alerts** — alarms (and rule alerts) on your phone through a free Telegram bot. See [Phone alerts](#phone-alerts-telegram).
- Closing the window keeps it watching in the taskbar (`close_to_taskbar`); **Quit** or `Ctrl+Q` closes it.
  `--minimized` starts it in the taskbar. Events go to `system_monitor.log`.

## Temperatures on Windows — install LibreHardwareMonitor (once)

Windows doesn't give programs the CPU temperature, so System Monitor reads it from
**LibreHardwareMonitor** (free, open source; works with Intel / AMD CPUs and NVIDIA / AMD / Intel GPUs):

1. Download it from <https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases> and unzip it.
2. Run `LibreHardwareMonitor.exe` **as administrator**.
3. *Options* → **Remote Web Server** → **Run** (port 8085). Also tick *Options* → *Start Minimized*,
   *Minimize To Tray* and *Run On Windows Startup* so it's always there.
4. Check <http://localhost:8085/data.json> opens in your browser — System Monitor picks it up within seconds.

Without it you still get: **NVIDIA GPUs** — temperature, usage, VRAM, power and fan from `nvidia-smi` (comes
with the driver); **any GPU** — usage from Windows' counters; **CPU** — an approximate ACPI temperature when
System Monitor runs as administrator (often missing or inaccurate). The top-right corner shows which
sources work.

## Process rules

One rule per line in `[rules]`:  `name = program  what > limit  for time  -> action`

| Part | Values |
|---|---|
| program | `chrome.exe`, `chrome` (the `.exe` is optional), or `*` = any program (Windows' own are skipped) |
| what | `cpu` (%), `gpu` (%), `memory` (% of RAM, or `MB` / `GB`) |
| compare | `>` `>=` `<` `<=` |
| time | `30 s`, `5 min`, `2 h` |
| action | `warn` (pop-up with End / Ignore), `end` (ends it by itself), `notify` (phone only) |

```ini
[rules]
stuck_app  = * cpu > 90 for 5 min -> warn
chrome_ram = chrome memory > 4 GB for 2 min -> warn
runaway    = python.exe cpu > 95 for 30 min -> end
gpu_hog    = * gpu > 90 for 10 min -> notify
```

A rule fires once per process; it can fire again after the process has gone back under the limit.
*Ignore* stops that rule for that process until it restarts. Rules that can't be read are listed in the status
bar when the settings load.

## Phone alerts (Telegram)

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name — it replies with a **token** like
   `7123456789:AAF…`. Put it in `system_monitor.ini`: `[telegram] bot_token = 7123456789:AAF…`
2. Open your new bot in Telegram and send it any message (e.g. *hi*).
3. Click **Test alarm** in System Monitor: it finds your chat by itself, saves `chat_id`, and you get
   *"System Monitor test — phone alerts work."*

`send = critical` (default) sends only critical temperatures; `warning` sends warnings too; `off` stops it.
`rules = yes` also sends process-rule alerts. The same alert is sent at most once every 10 minutes. The token
is stored in `system_monitor.ini` (it's in `.gitignore` — don't share it).

## `system_monitor.ini`

```ini
[alarm]
cpu_temp_warning = 85       ; °C
cpu_temp_critical = 95
gpu_temp_warning = 83
gpu_temp_critical = 92
ram_warning = 92            ; % (0 = no alarm)
ram_critical = 0
hold_seconds = 10           ; must stay that high this long (critical: 5 s at most)
sound = yes
snooze_minutes = 10

[critical]
action = ask                ; ask | shutdown | sleep | kill_top | none
countdown_seconds = 60

[monitor]
refresh_ms = 1000
history_seconds = 120
temperature_unit = C        ; C | F
close_to_taskbar = yes
librehardwaremonitor_url = http://localhost:8085/data.json
use_nvidia_smi = yes
use_acpi = yes

[overlay]
enabled = yes
corner = bottom-right       ; bottom-right | bottom-left | top-right | top-left
opacity = 90                ; %
show_ram = no               ; also show RAM use
click_through = no          ; yes = clicks go to the window underneath

[cooldown]
enabled = yes
trigger = warning           ; warning | critical
watch = cpu, gpu            ; which temperatures can start it
method = max_cpu            ; max_cpu | power_saver
max_cpu_percent = 80
restore_after_minutes = 3

[history]
enabled = yes
keep_days = 90

[rules]
stuck_app = * cpu > 90 for 5 min -> warn

[telegram]
bot_token =
chat_id =                   ; found by itself after you message the bot
send = critical             ; critical | warning | off
rules = yes

[processes]
protected = System, csrss.exe, winlogon.exe, … ; never ended
show = 40
```

Typical safe limits: most desktop CPUs throttle around 95–100 °C and GPUs around 83–90 °C — check your
model's maximum (Intel "TjMax", AMD "Tjmax", NVIDIA "max GPU temperature") and set *critical* a few degrees
below it. Laptops often run hotter than desktops under load; that's normal up to their limit.

## Files

Everything is kept next to `system_monitor.py` (or `SystemMonitor.exe`):

| File | What it is |
|---|---|
| `system_monitor.py` | The app |
| `system_monitor.ini` | Your settings — created on first run, applied a few seconds after you save it |
| `system_monitor.log` | Events and errors |
| `system_monitor_history.csv` | Temperature history (10-minute summaries) |
| `system_monitor_cooldown.json` | Only during a cool-down: the setting to put back |
| `build.bat` · `requirements.txt` | Build `SystemMonitor.exe` · the package it needs |

Moving from an older version that lived next to Window Manager: put this folder next to `window_manager.py`;
on first start it moves your old `system_monitor.ini`, history and cool-down file into this folder.

## Troubleshooting

- **No CPU temperature** — install LibreHardwareMonitor and turn on its web server (see
  *Temperatures on Windows*). If it's running but not picked up, open <http://localhost:8085/data.json>; if you changed
  its port, change `librehardwaremonitor_url`.
- **The overlay isn't visible in a game** — games in *exclusive* full screen draw over every window; switch
  the game to *borderless* / *windowed full screen*.
- **Cool-down does nothing / "access denied"** — changing the processor state can need administrator rights:
  run System Monitor as administrator. `powercfg /q SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX` in a terminal
  shows the current value. If a cool-down was interrupted, starting System Monitor again puts it back.
- **No GPU column values** — the per-app GPU numbers come from Windows' GPU counters (Windows 10 1709+); they
  stay empty for apps that don't use the graphics card.
- **Telegram: "Unauthorized" / "chat not found"** — check the token; send a message to the bot, then *Test alarm*.
- **Can't end a process** — *access denied*: run System Monitor as administrator.
- **Python's icon in the taskbar instead of System Monitor's** — Windows caches taskbar icons; unpin and pin it
  again, or use `SystemMonitor.exe`.

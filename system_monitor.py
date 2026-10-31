"""
System Monitor for Windows — CPU, GPU and RAM usage and temperatures, with an alarm when it gets too hot.

- Live cards for CPU, GPU and RAM: usage, temperature, details and a 2-minute history chart.
- Processes using the most CPU / memory: end a process (or its whole tree), open its folder.
- Alarm when the CPU or GPU temperature (or RAM use) stays too high: pop-up + sound, the processes
  using the most CPU, and actions — end a process, sleep, shut down (now or after a countdown).
- Optional automatic action when it's critical: shutdown, sleep or end the busiest process,
  after a countdown you can cancel.

Temperatures come from (the first one that works):
  1. LibreHardwareMonitor's web server (free, any CPU / GPU) — see the README, "System monitor".
  2. nvidia-smi for NVIDIA graphics cards (comes with the driver).
  3. Windows' ACPI thermal zone (approximate; needs administrator).
CPU / RAM usage come from psutil; GPU usage from nvidia-smi, LibreHardwareMonitor or Windows'
GPU performance counters (any GPU).

Run:      python system_monitor.py          (--minimized to start in the taskbar)
Icon:     python system_monitor.py icon system_monitor.ico   (used by build.bat)
Requires: Windows 10/11, Python 3.8+, pip install psutil
Settings: system_monitor.ini next to this file (applied a few seconds after you save it).
A stand-alone app: everything it needs and keeps is in its own folder. See README.md.
"""

import configparser
import ctypes
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import zlib
import base64
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

try:
    import psutil
except Exception:  # checked at start-up with a clear message
    psutil = None

IS_WIN = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
SETTINGS_FILE = os.path.join(APP_DIR, "system_monitor.ini")
LOG_FILE = os.path.join(APP_DIR, "system_monitor.log")
HISTORY_FILE = os.path.join(APP_DIR, "system_monitor_history.csv")   # 10-minute temperature summaries
COOLDOWN_FILE = os.path.join(APP_DIR, "system_monitor_cooldown.json")  # settings to put back after a cool-down
APP_ID = "Mythos.SystemMonitor"  # its own taskbar button and icon (not Python's)
VERSION = "v4"
TITLE = "System Monitor"

T = {
    "bg": "#131418", "surface": "#1b1d23", "field": "#22252d", "border": "#2b2e38",
    "fg": "#e8e9ed", "muted": "#9095a1", "faint": "#5f6470",
    "accent": "#8aa4ff", "select": "#2a3456", "hover": "#1d2028",
    "ok": "#7bd88f", "warn": "#f0b46a", "crit": "#ff6b6b", "temp": "#ffa25c",
}
UI_FONT = "Segoe UI"


def log(text):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + text + "\n")
    except OSError:
        pass


# ======================================================================
# Settings — system_monitor.ini
# ======================================================================

DEFAULT_SETTINGS = """\
; ======================================================================
;  System Monitor settings
;  Edit and save this file: the monitor applies changes within a few seconds.
; ======================================================================

[alarm]
; Temperatures in °C. warning = pop-up and sound; critical = also the critical action below.
cpu_temp_warning = 85
cpu_temp_critical = 95
gpu_temp_warning = 83
gpu_temp_critical = 92

; RAM use in % (0 = no alarm).
ram_warning = 92
ram_critical = 0

; The value must stay that high for this many seconds before the alarm goes off
; (short spikes are normal). Critical goes off after at most 5 seconds.
hold_seconds = 10

; Play a sound with the alarm (repeated every few seconds while it's critical).
sound = yes

; "Snooze" on the alarm: minutes before a warning can show again (critical always shows).
snooze_minutes = 10

[critical]
; What to do by itself when a temperature is critical:
;   ask      = only show the alarm with buttons (default)
;   shutdown = shut down the PC after the countdown
;   sleep    = put the PC to sleep after the countdown
;   kill_top = end the process using the most CPU after the countdown
;   none     = nothing, not even the pop-up's countdown
action = ask

; Seconds of countdown before the action (you can cancel it from the pop-up).
countdown_seconds = 60

[monitor]
; How often the values are read, in milliseconds.
refresh_ms = 1000

; How many seconds of history the charts show.
history_seconds = 120

; C or F.
temperature_unit = C

; Closing the window keeps the monitor running in the taskbar (so alarms still work).
close_to_taskbar = yes

; LibreHardwareMonitor's web server (Options → Remote Web Server → Run). Empty = don't use it.
librehardwaremonitor_url = http://localhost:8085/data.json

; Use nvidia-smi (NVIDIA graphics cards) and Windows' ACPI thermal zone (approximate CPU temperature).
use_nvidia_smi = yes
use_acpi = yes

[overlay]
; A small always-on-top strip with CPU and GPU usage and temperature, in a corner of the main screen.
; It stays there while System Monitor runs (also when its window is minimized).
enabled = yes

; bottom-right | bottom-left | top-right | top-left
corner = bottom-right

; Opacity in % (30-100).
opacity = 90

; Also show RAM use.
show_ram = no

; yes = clicks go through it to the window underneath (then it can't be moved or right-clicked).
click_through = no

[cooldown]
; Cool-down mode: when the CPU or GPU is hot, limit the CPU's top speed (less heat, a little slower),
; then put it back once it has cooled down. Gentler than shutting down.
enabled = yes

; When: warning | critical (the limits in [alarm]).
trigger = warning

; Which temperatures can start it: cpu, gpu or both (on many laptops the CPU and GPU share one cooler).
watch = cpu, gpu

; How:
;   max_cpu     = set Windows' "maximum processor state" to max_cpu_percent (works on every PC) (default)
;   power_saver = switch to Windows' Power saver plan (if your PC has it)
method = max_cpu
max_cpu_percent = 80

; Put things back after the temperature has stayed 5 °C under its warning limit for this many minutes.
restore_after_minutes = 3

[history]
; Keep a summary of temperatures and usage every 10 minutes (min / average / max), for the History window.
enabled = yes
keep_days = 90

[rules]
; Watch processes. One rule per line:   name = process  what > limit  for time  -> action
;   process: a program name (chrome.exe, or chrome) or * for any program
;   what:    cpu (%), gpu (%), memory (% of RAM, or with MB / GB)
;   action:  warn   = a pop-up with End / Ignore
;            end    = end the process by itself (never the protected ones)
;            notify = only send it to your phone (see [telegram])
; Examples:
;   stuck_app  = * cpu > 90 for 5 min -> warn
;   chrome_ram = chrome memory > 4 GB for 2 min -> warn
;   runaway    = python.exe cpu > 95 for 30 min -> end
;   gpu_hog    = * gpu > 90 for 10 min -> notify
stuck_app = * cpu > 90 for 5 min -> warn

[telegram]
; Send alarms to your phone with a free Telegram bot (see the README, "Phone alerts"):
;   1. In Telegram, talk to @BotFather → /newbot → copy the token it gives you here.
;   2. Send any message to your new bot. System Monitor finds the chat_id by itself.
bot_token =
chat_id =

; What to send: critical | warning (warnings and critical) | off
send = critical

; Also send process-rule alerts (warn / end / notify).
rules = yes

[processes]
; Never end these (ending them would crash Windows). Names, separated by commas.
protected = System, Registry, smss.exe, csrss.exe, wininit.exe, winlogon.exe, services.exe, lsass.exe,
    svchost.exe, fontdrvhost.exe, dwm.exe, Memory Compression, MsMpEng.exe, explorer.exe

; How many processes the list shows.
show = 40
"""

SETTINGS = {}
SETTINGS_WARNINGS = []


def upgrade_settings_file():
    """Add options that are new in this version to an existing file, keeping your values."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
    try:
        cp.read_string(text)
    except configparser.Error:
        return
    defaults = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
    defaults.read_string(DEFAULT_SETTINGS)
    added = []
    for sec in defaults.sections():
        if not cp.has_section(sec):
            text = text.rstrip("\n") + f"\n\n[{sec}]\n" + "".join(f"{k} = {v}\n" for k, v in defaults[sec].items())
            added.append(f"[{sec}]")
            continue
        missing = [k for k in defaults[sec] if not cp.has_option(sec, k)]
        if missing:
            m = re.search(rf"^\[{sec}\]\s*$", text, re.M)
            nxt = re.search(r"^\[", text[m.end():], re.M)
            pos = m.end() + nxt.start() if nxt else len(text)
            block = "".join(f"{k} = {defaults[sec][k]}\n" for k in missing)
            text = text[:pos].rstrip("\n") + "\n" + block + ("\n" + text[pos:] if nxt else "")
            added += missing
    if added:
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            log("settings: added " + ", ".join(added))
        except OSError:
            pass


def load_settings():
    global SETTINGS, SETTINGS_WARNINGS
    warnings = []
    if not os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                f.write(DEFAULT_SETTINGS)
        except OSError:
            warnings.append("couldn't create system_monitor.ini")
    else:
        upgrade_settings_file()
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
    try:
        cp.read_string(DEFAULT_SETTINGS)
        cp.read(SETTINGS_FILE, encoding="utf-8")
    except configparser.Error as e:
        warnings.append(f"system_monitor.ini has an error ({e}); using defaults")
        cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
        cp.read_string(DEFAULT_SETTINGS)
    al, cr, mo, pr = cp["alarm"], cp["critical"], cp["monitor"], cp["processes"]

    def num(sec, key, default, lo, hi, cast=float):
        try:
            return cast(min(hi, max(lo, float(str(sec.get(key, default)).replace(",", ".")))))
        except ValueError:
            warnings.append(f"{key} must be a number; using {default}")
            return cast(default)

    def yes(sec, key, default="yes"):
        return str(sec.get(key, default)).strip().lower() in ("1", "yes", "true", "on")

    ov = cp["overlay"] if cp.has_section("overlay") else {}
    corner = str(ov.get("corner", "bottom-right")).strip().lower().replace(" ", "-").replace("_", "-")
    if corner not in ("bottom-right", "bottom-left", "top-right", "top-left"):
        warnings.append(f"[overlay] corner = “{corner}” isn't bottom-right, bottom-left, top-right or top-left")
        corner = "bottom-right"
    cd = cp["cooldown"] if cp.has_section("cooldown") else {}
    hi = cp["history"] if cp.has_section("history") else {}
    tg = cp["telegram"] if cp.has_section("telegram") else {}
    cd_method = str(cd.get("method", "max_cpu")).strip().lower().replace("-", "_")
    if cd_method not in ("max_cpu", "power_saver"):
        warnings.append(f"[cooldown] method = “{cd_method}” isn't max_cpu or power_saver; using max_cpu")
        cd_method = "max_cpu"
    cd_trigger = str(cd.get("trigger", "warning")).strip().lower()
    if cd_trigger not in ("warning", "critical"):
        warnings.append("[cooldown] trigger must be warning or critical; using warning")
        cd_trigger = "warning"
    cd_watch = {w.strip().lower() for w in str(cd.get("watch", "cpu, gpu")).split(",")} & {"cpu", "gpu"}
    tg_send = str(tg.get("send", "critical")).strip().lower()
    if tg_send not in ("critical", "warning", "off"):
        warnings.append("[telegram] send must be critical, warning or off; using critical")
        tg_send = "critical"
    rules = []
    if cp.has_section("rules"):
        for key, text in cp["rules"].items():
            r = parse_rule(text)
            if isinstance(r, str):
                warnings.append(f"[rules] {key}: {r}")
            else:
                r["key"] = key
                rules.append(r)
    action = cr.get("action", "ask").strip().lower().replace("-", "_")
    if action not in ("ask", "shutdown", "sleep", "kill_top", "none"):
        warnings.append(f"[critical] action = “{action}” isn't ask, shutdown, sleep, kill_top or none; using ask")
        action = "ask"
    unit = mo.get("temperature_unit", "C").strip().upper()[:1]
    if unit not in ("C", "F"):
        warnings.append("temperature_unit must be C or F; using C")
        unit = "C"
    SETTINGS = {
        "cpu_warn": num(al, "cpu_temp_warning", 85, 0, 150), "cpu_crit": num(al, "cpu_temp_critical", 95, 0, 150),
        "gpu_warn": num(al, "gpu_temp_warning", 83, 0, 150), "gpu_crit": num(al, "gpu_temp_critical", 92, 0, 150),
        "ram_warn": num(al, "ram_warning", 92, 0, 100), "ram_crit": num(al, "ram_critical", 0, 0, 100),
        "hold": num(al, "hold_seconds", 10, 0, 600), "sound": yes(al, "sound"),
        "snooze": num(al, "snooze_minutes", 10, 1, 1440),
        "action": action, "countdown": num(cr, "countdown_seconds", 60, 5, 3600, int),
        "refresh_ms": num(mo, "refresh_ms", 1000, 250, 10000, int),
        "history": num(mo, "history_seconds", 120, 20, 3600, int), "unit": unit,
        "close_to_taskbar": yes(mo, "close_to_taskbar"),
        "lhm_url": mo.get("librehardwaremonitor_url", "").strip(),
        "use_nvidia": yes(mo, "use_nvidia_smi"), "use_acpi": yes(mo, "use_acpi"),
        "protected": {p.strip().lower() for p in pr.get("protected", "").replace("\n", ",").split(",") if p.strip()},
        "show": num(pr, "show", 40, 5, 500, int),
        "ov_on": yes(ov, "enabled"), "ov_corner": corner, "ov_alpha": num(ov, "opacity", 90, 30, 100) / 100,
        "ov_ram": yes(ov, "show_ram", "no"), "ov_through": yes(ov, "click_through", "no"),
        "cd_on": yes(cd, "enabled"), "cd_trigger": cd_trigger, "cd_watch": cd_watch or {"cpu"},
        "cd_method": cd_method, "cd_max": num(cd, "max_cpu_percent", 80, 30, 99, int),
        "cd_restore": num(cd, "restore_after_minutes", 3, 0.5, 120),
        "hist_on": yes(hi, "enabled"), "hist_days": num(hi, "keep_days", 90, 1, 3650, int),
        "rules": rules,
        "tg_token": str(tg.get("bot_token", "")).strip(), "tg_chat": str(tg.get("chat_id", "")).strip(),
        "tg_send": tg_send, "tg_rules": yes(tg, "rules"),
    }
    for a, b in (("cpu_warn", "cpu_crit"), ("gpu_warn", "gpu_crit"), ("ram_warn", "ram_crit")):
        if SETTINGS[a] and SETTINGS[b] and SETTINGS[b] < SETTINGS[a]:
            warnings.append(f"{b.replace('_crit', '')} critical is lower than its warning; check [alarm]")
    SETTINGS_WARNINGS = warnings
    for w in warnings:
        log("settings: " + w)
    return warnings


def fmt_temp(c):
    if c is None:
        return "—"
    if SETTINGS.get("unit") == "F":
        return f"{c * 9 / 5 + 32:.0f} °F"
    return f"{c:.0f} °C"


def fmt_bytes(n):
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


# ======================================================================
# Sensors
# ======================================================================

def parse_number(text):
    """'45.3 °C', '45,3 °C', '12 %', '1024.0 MB' → 45.3 … (None if there's no number)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(text))
    return float(m.group(0).replace(",", ".")) if m else None


def _hw_kind(node, sensor_ids):
    img = (node.get("ImageURL") or "").lower()
    ids = " ".join(sensor_ids).lower()
    if re.search(r"^/(amdcpu|intelcpu|cpu)\b", ids):
        return "cpu"
    if re.search(r"^/(gpu-|nvidiagpu|atigpu|amdgpu|intelgpu)", ids):
        return "gpu"
    if "cpu.png" in img:
        return "cpu"
    if any(x in img for x in ("nvidia.png", "ati.png", "amd.png", "gpu", "intel.png")):
        return "gpu"
    return None


def parse_lhm(tree):
    """LibreHardwareMonitor / OpenHardwareMonitor data.json → {"cpu": {...}, "gpus": [...]}."""
    hardware = []  # [(kind, name, [(group, sensor name, value text, sensor id)])]

    def walk(node):
        kids = node.get("Children") or []
        # a hardware node: its children are sensor groups (Temperatures, Load, …) whose children are sensors
        groups = [k for k in kids if k.get("Children") and all(not g.get("Children") for g in k["Children"])]
        if groups and node.get("ImageURL", "").lower().find("transparent") < 0:
            sensors = []
            for g in groups:
                for s in g["Children"]:
                    if s.get("Value") not in (None, ""):
                        sensors.append((g.get("Text", ""), s.get("Text", ""), s.get("Value"), s.get("SensorId", "")))
            kind = _hw_kind(node, [x[3] for x in sensors if x[3]]) or "other"
            hardware.append((kind, node.get("Text", ""), sensors))
        for k in kids:
            if k not in groups:
                walk(k)

    walk(tree)
    cpu, gpus, all_fans = {}, [], []
    for kind, name, sensors in hardware:
        for g, sname, v, _sid in sensors:
            if g.lower() == "fans" and parse_number(v) is not None and "rpm" in str(v).lower():
                all_fans.append({"hw": name, "name": sname, "rpm": parse_number(v), "kind": kind})
        temps = {s: parse_number(v) for g, s, v, _ in sensors if g.lower().startswith("temperature")
                 and "distance" not in s.lower() and parse_number(v) is not None}
        loads = {s: parse_number(v) for g, s, v, _ in sensors if g.lower() == "load"}
        data = {s: (parse_number(v), str(v)) for g, s, v, _ in sensors if "data" in g.lower()}
        power = {s: parse_number(v) for g, s, v, _ in sensors if g.lower() == "powers"}
        fans = {s: (parse_number(v), str(v)) for g, s, v, _ in sensors if g.lower() in ("fans", "controls")}
        if kind == "cpu" and not cpu:
            pick = next((temps[n] for n in ("CPU Package", "Core (Tctl/Tdie)", "Core (Tctl)", "Tctl/Tdie", "Tdie",
                                            "Core Max", "CPU", "Package") if n in temps), None)
            if pick is None and temps:
                pick = max(temps.values())
            cpu = {"name": name, "temp": pick, "temps": temps, "load": loads.get("CPU Total"),
                   "power": power.get("CPU Package") or power.get("Package")}
        elif kind == "gpu":
            temp = temps.get("GPU Core") or temps.get("GPU") or (next(iter(temps.values())) if temps else None)

            def mb(key):
                v = data.get(key)
                if not v or v[0] is None:
                    return None
                return v[0] * (1024 ** 3 if "GB" in v[1] else 1024 ** 2)
            fan = next((v for k, v in fans.items() if "gpu" in k.lower() or "fan" in k.lower()), None)
            gpus.append({"name": name, "temp": temp, "hotspot": temps.get("GPU Hot Spot") or temps.get("GPU Hotspot"),
                         "load": loads.get("GPU Core") if loads.get("GPU Core") is not None else loads.get("D3D 3D"),
                         "mem_used": mb("GPU Memory Used") or mb("D3D Dedicated Memory Used"),
                         "mem_total": mb("GPU Memory Total"),
                         "power": next((v for k, v in power.items() if "gpu" in k.lower() or "package" in k.lower()),
                                       None),
                         "fan": (f"{fan[0]:.0f} {'%' if '%' in fan[1] else 'RPM'}" if fan and fan[0] is not None
                                 else None),
                         "fan_value": fan[0] if fan else None,
                         "source": "LibreHardwareMonitor"})
    board = [f for f in all_fans if f["kind"] != "gpu"]
    cpu_fan = next((f for f in board if "cpu" in f["name"].lower()), board[0] if board else None)
    if cpu:
        cpu["fan_rpm"] = cpu_fan["rpm"] if cpu_fan else None
    return {"cpu": cpu, "gpus": gpus, "fans": all_fans}


def parse_nvidia_line(line):
    """One line of nvidia-smi --format=csv,noheader,nounits → a GPU record (None if it isn't one)."""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 7:
        return None

    def n(i):
        return parse_number(parts[i]) if parts[i] and "N/A" not in parts[i] and "Not Supported" not in parts[i] \
            else None
    return {"name": parts[0], "temp": n(1), "load": n(2),
            "mem_used": n(3) * 1024 ** 2 if n(3) is not None else None,
            "mem_total": n(4) * 1024 ** 2 if n(4) is not None else None,
            "power": n(5), "fan": f"{n(6):.0f} %" if n(6) is not None else None, "fan_value": n(6), "hotspot": None,
            "source": "nvidia-smi"}


NVIDIA_QUERY = "name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,fan.speed"


class Sensors:
    """Reads every source in background threads; latest() returns the newest values."""

    def __init__(self):
        self.lock = threading.Lock()
        self.lhm = None            # parsed LibreHardwareMonitor data
        self.lhm_error = None
        self.nvidia = []           # GPU records from nvidia-smi
        self.nvidia_state = "off"
        self.acpi_temp = None
        self.linux_temp = None
        self.pdh = None
        self.proc_gpu = {}         # pid -> GPU % (Windows counters)
        self.stop = threading.Event()
        self.threads = []

    def start(self):
        for fn in (self._lhm_loop, self._nvidia_loop, self._acpi_loop, self._linux_loop):
            t = threading.Thread(target=self._guard, args=(fn,), daemon=True)
            t.start()
            self.threads.append(t)
        if IS_WIN:
            try:
                self.pdh = PdhGpu()
            except Exception:
                log("GPU counters unavailable:\n" + traceback.format_exc())
                self.pdh = None

    def _guard(self, fn):
        try:
            fn()
        except Exception:
            log(f"sensor thread {fn.__name__} crashed:\n" + traceback.format_exc())

    # ---- LibreHardwareMonitor web server
    def _lhm_loop(self):
        fails = 0
        while not self.stop.is_set():
            url = SETTINGS.get("lhm_url")
            if not url:
                self.lhm, self.lhm_error = None, "off"
                self.stop.wait(3)
                continue
            try:
                with urllib.request.urlopen(url, timeout=1.5) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                parsed = parse_lhm(data)
                with self.lock:
                    self.lhm, self.lhm_error = parsed, None
                fails = 0
            except Exception as e:
                fails += 1
                with self.lock:
                    self.lhm, self.lhm_error = None, f"{type(e).__name__}"
            # not running: check now and then only
            self.stop.wait(SETTINGS.get("refresh_ms", 1000) / 1000 if fails < 3 else 10)

    # ---- nvidia-smi (one process that prints a line every refresh)
    def _nvidia_loop(self):
        exe = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe"
                                             if IS_WIN and os.path.isfile(r"C:\Windows\System32\nvidia-smi.exe")
                                             else None)
        if not exe:
            self.nvidia_state = "not found"
            return
        try:
            listing = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=15,
                                     creationflags=CREATE_NO_WINDOW if IS_WIN else 0).stdout
        except (OSError, subprocess.SubprocessError):
            listing = ""
        n_gpus = len([x for x in listing.splitlines() if x.strip().startswith("GPU ")])
        if not n_gpus:
            self.nvidia_state = "no NVIDIA GPU"
            return
        while not self.stop.is_set():
            if not SETTINGS.get("use_nvidia", True):
                self.nvidia, self.nvidia_state = [], "off"
                self.stop.wait(3)
                continue
            ms = SETTINGS.get("refresh_ms", 1000)
            try:
                p = subprocess.Popen([exe, f"--query-gpu={NVIDIA_QUERY}", "--format=csv,noheader,nounits",
                                      "-lms", str(ms)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
            except OSError as e:
                self.nvidia_state = f"error: {e}"
                return
            self.nvidia_state = "running"
            count, batch = 0, []
            try:
                for line in p.stdout:
                    if self.stop.is_set() or SETTINGS.get("refresh_ms", 1000) != ms \
                            or not SETTINGS.get("use_nvidia", True):
                        break
                    rec = parse_nvidia_line(line)
                    if not rec:
                        continue
                    count += 1
                    batch.append(rec)
                    if len(batch) >= n_gpus:  # one line per GPU each round
                        with self.lock:
                            self.nvidia = batch
                        batch = []
            finally:
                try:
                    p.kill()
                except OSError:
                    pass
            if count == 0:
                self.nvidia_state = "no NVIDIA GPU"
                return
            self.stop.wait(1)

    # ---- ACPI thermal zone (approximate CPU temperature; Windows, administrator)
    def _acpi_loop(self):
        if not IS_WIN:
            return
        self.stop.wait(6)  # give LibreHardwareMonitor a chance first
        while not self.stop.is_set():
            have_cpu = bool(self.lhm and self.lhm.get("cpu", {}).get("temp") is not None)
            if SETTINGS.get("use_acpi", True) and not have_cpu:
                try:
                    out = subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         "(Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature "
                         "-ErrorAction Stop | Select-Object -ExpandProperty CurrentTemperature) -join ','"],
                        capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW).stdout
                    vals = [parse_number(x) for x in out.strip().split(",") if x.strip()]
                    temps = [v / 10 - 273.15 for v in vals if v]
                    temps = [t for t in temps if 5 < t < 130]
                    self.acpi_temp = max(temps) if temps else None
                except (OSError, subprocess.SubprocessError):
                    self.acpi_temp = None
            else:
                self.acpi_temp = None
            self.stop.wait(5)

    # ---- Linux / other systems (psutil sensors)
    def _linux_loop(self):
        if IS_WIN or psutil is None or not hasattr(psutil, "sensors_temperatures"):
            return
        while not self.stop.is_set():
            try:
                found = psutil.sensors_temperatures()
                best = None
                for chip in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
                    for e in found.get(chip, []):
                        if e.current and (best is None or e.label.lower().startswith(("package", "tctl", "tdie"))):
                            best = e.current
                    if best is not None:
                        break
                self.linux_temp = best
            except Exception:
                self.linux_temp = None
            self.stop.wait(2)

    # ---- everything together
    def latest(self):
        with self.lock:
            lhm = self.lhm
            nv = list(self.nvidia)
        cpu = {"temp": None, "temp_source": None, "name": None, "power": None, "fan_rpm": None}
        if lhm and lhm.get("cpu"):
            c = lhm["cpu"]
            cpu.update(name=c.get("name"), power=c.get("power"), fan_rpm=c.get("fan_rpm"))
            if c.get("temp") is not None:
                cpu.update(temp=c["temp"], temp_source="LibreHardwareMonitor")
        if cpu["temp"] is None and self.linux_temp is not None:
            cpu.update(temp=self.linux_temp, temp_source="sensors")
        if cpu["temp"] is None and self.acpi_temp is not None:
            cpu.update(temp=self.acpi_temp, temp_source="ACPI (approximate)")
        gpus = [dict(g) for g in nv]
        for g in (lhm or {}).get("gpus", []):
            same = next((x for x in gpus if _same_gpu(x["name"], g["name"])), None)
            if same:  # nvidia-smi first; fill the gaps from LibreHardwareMonitor
                for k, v in g.items():
                    if same.get(k) is None and v is not None:
                        same[k] = v
            else:
                gpus.append(dict(g))
        load = None
        if self.pdh is not None:
            try:
                load, per_pid = self.pdh.read()
                self.proc_gpu = per_pid
            except Exception:
                load = None
            if load is not None:
                if not gpus:
                    gpus.append({"name": "GPU", "temp": None, "load": load, "source": "Windows counters"})
                else:
                    for g in gpus:
                        if g.get("load") is None:
                            g["load"] = load
                            break
        # the GPU to show first: the busiest discrete one (most memory)
        gpus.sort(key=lambda g: (-(g.get("mem_total") or 0), g["name"]))
        return {"cpu": cpu, "gpus": gpus, "fans": (lhm or {}).get("fans", [])}

    def sources(self):
        lhm = "on" if self.lhm else ("off" if self.lhm_error == "off" else "not running")
        return {"LibreHardwareMonitor": lhm, "nvidia-smi": self.nvidia_state,
                "GPU counters": "on" if self.pdh is not None else "off"}


def _same_gpu(a, b):
    a, b = re.sub(r"[^a-z0-9]", "", a.lower()), re.sub(r"[^a-z0-9]", "", b.lower())
    return a == b or a in b or b in a


class PdhGpu:
    """GPU usage from Windows' performance counters (works with any GPU, like Task Manager)."""
    PATH = r"\GPU Engine(*)\Utilization Percentage"

    class _ITEM(ctypes.Structure):
        class _VAL(ctypes.Structure):
            _fields_ = [("CStatus", ctypes.c_uint32), ("pad", ctypes.c_uint32), ("doubleValue", ctypes.c_double)]
        _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _VAL)]

    def __init__(self):
        self.pdh = ctypes.windll.pdh
        self.query, self.counter = ctypes.c_void_p(), ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQuery failed")
        if self.pdh.PdhAddEnglishCounterW(self.query, self.PATH, None, ctypes.byref(self.counter)) != 0:
            raise OSError("no GPU engine counters")
        self.pdh.PdhCollectQueryData(self.query)

    def read(self):
        """(GPU % like Task Manager — the busiest engine, {pid: GPU %})."""
        if self.pdh.PdhCollectQueryData(self.query) != 0:
            return None, {}
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        PDH_MORE_DATA = 0x800007D2
        r = self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x200, ctypes.byref(size), ctypes.byref(count), None)
        if (r & 0xFFFFFFFF) != PDH_MORE_DATA or not size.value:
            return (0.0 if (r & 0xFFFFFFFF) in (0, PDH_MORE_DATA) else None), {}
        buf = ctypes.create_string_buffer(size.value)
        if self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x200, ctypes.byref(size), ctypes.byref(count),
                                                 buf) != 0:
            return None, {}
        items = ctypes.cast(buf, ctypes.POINTER(self._ITEM))
        return gpu_engine_totals([(items[i].szName or "", items[i].FmtValue.doubleValue)
                                  for i in range(count.value) if items[i].FmtValue.CStatus in (0, 1)])


def gpu_engine_totals(samples):
    """[(instance name, %)] of "GPU Engine" counters → (overall %, {pid: %}).
    Like Task Manager: add up each engine type (3D, Copy, VideoDecode, …), then take the busiest one."""
    per_engine, per_pid = {}, {}
    for name, value in samples:
        m = re.search(r"pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+).*?engtype_(\w+)", name, re.I)
        if not m:
            continue
        pid, luid, eng = int(m.group(1)), m.group(2).lower(), m.group(3)
        per_engine[(luid, eng)] = per_engine.get((luid, eng), 0.0) + value
        d = per_pid.setdefault(pid, {})
        d[eng] = d.get(eng, 0.0) + value
    overall = min(100.0, max(per_engine.values())) if per_engine else 0.0
    return overall, {pid: min(100.0, max(d.values())) for pid, d in per_pid.items()}


# ======================================================================
# Process rules:  "chrome memory > 4 GB for 2 min -> warn"
# ======================================================================

RULE_RE = re.compile(
    r"^\s*(?P<proc>\S+)\s+(?P<metric>cpu|gpu|memory|mem|ram)\s*(?P<op>>=|>|<=|<)\s*(?P<val>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>%|gb|mb|kb)?\s+for\s+(?P<dur>\d+(?:[.,]\d+)?)\s*(?P<dunit>s|secs?|seconds?|m|mins?|minutes?|h|hours?)"
    r"\s*->\s*(?P<action>warn|end|kill|notify)\s*$", re.I)


def parse_rule(text):
    """A rule as a dict, or an error message (str)."""
    m = RULE_RE.match(text or "")
    if not m:
        return "can't read it — write it like:  chrome memory > 4 GB for 2 min -> warn"
    metric = {"mem": "memory", "ram": "memory"}.get(m.group("metric").lower(), m.group("metric").lower())
    val = float(m.group("val").replace(",", "."))
    unit = (m.group("unit") or "").lower()
    if metric == "memory" and unit in ("gb", "mb", "kb"):
        val *= {"gb": 1024 ** 3, "mb": 1024 ** 2, "kb": 1024}[unit]
        unit = "bytes"
    elif metric == "memory":
        unit = "%"
    elif unit not in ("", "%"):
        return f"{metric} is in % (not {unit.upper()})"
    dur = float(m.group("dur").replace(",", ".")) * {"s": 1, "m": 60, "h": 3600}[m.group("dunit")[0].lower()]
    action = "end" if m.group("action").lower() == "kill" else m.group("action").lower()
    proc = m.group("proc").lower()
    return {"text": text.strip(), "proc": proc, "metric": metric, "op": m.group("op"), "value": val,
            "unit": unit, "seconds": dur, "action": action}


def rule_matches_name(rule, name):
    p, n = rule["proc"], (name or "").lower()
    return p == "*" or n == p or n == p + ".exe" or (n.endswith(".exe") and n[:-4] == p)


def rule_value(rule, proc, ram_total):
    if rule["metric"] == "cpu":
        return proc.get("cpu")
    if rule["metric"] == "gpu":
        return proc.get("gpu")
    if rule["unit"] == "%":
        return proc.get("mem", 0) * 100 / ram_total if ram_total else None
    return proc.get("mem")


def fmt_rule_value(rule, v):
    if v is None:
        return "—"
    return fmt_bytes(v) if rule["unit"] == "bytes" else f"{v:.0f} %"


class RuleEngine:
    def __init__(self):
        self.since = {}    # (rule key, pid) -> when the condition started
        self.fired = set() # (rule key, pid) already reported (until the condition stops)
        self.ignored = set()  # (rule key, pid) the user said "Ignore" to

    def update(self, now, procs, rules, ram_total):
        """Returns [(rule, process, value)] that just reached their time."""
        out, alive = [], set()
        for r in rules:
            for p in procs:
                if not rule_matches_name(r, p["name"]):
                    continue
                if r["proc"] == "*" and is_protected(p["name"], p["pid"]):
                    continue
                v = rule_value(r, p, ram_total)
                key = (r["key"], p["pid"])
                op = r["op"]
                hit = v is not None and (v > r["value"] if op == ">" else v >= r["value"] if op == ">=" else
                                         v < r["value"] if op == "<" else v <= r["value"])
                if not hit:
                    continue
                alive.add(key)
                start = self.since.setdefault(key, now)
                if now - start >= r["seconds"] and key not in self.fired and key not in self.ignored:
                    self.fired.add(key)
                    out.append((r, p, v))
        for key in [k for k in self.since if k not in alive]:
            del self.since[key]
            self.fired.discard(key)
        return out


# ======================================================================
# Temperature history: one line every 10 minutes
# ======================================================================

HIST_FIELDS = ("start", "cpu_min", "cpu_avg", "cpu_max", "gpu_min", "gpu_avg", "gpu_max", "cpu_load", "gpu_load",
               "cpu_w", "gpu_w")


class HistoryStore:
    BUCKET = 600

    def __init__(self, path=None):
        self.path = path or HISTORY_FILE
        self.cur = None   # {"start": t, "cpu": [...], "gpu": [...], ...}

    def add(self, t, cpu_t, gpu_t, cpu_l, gpu_l, cpu_w, gpu_w):
        start = int(t // self.BUCKET * self.BUCKET)
        if self.cur and self.cur["start"] != start:
            self.flush()
        if not self.cur:
            self.cur = {"start": start, "cpu": [], "gpu": [], "cl": [], "gl": [], "cw": [], "gw": []}
        for k, v in (("cpu", cpu_t), ("gpu", gpu_t), ("cl", cpu_l), ("gl", gpu_l), ("cw", cpu_w), ("gw", gpu_w)):
            if v is not None:
                self.cur[k].append(float(v))

    def flush(self):
        c, self.cur = self.cur, None
        if not c or not any(c[k] for k in ("cpu", "gpu", "cl")):
            return

        def stats(xs):
            return (min(xs), sum(xs) / len(xs), max(xs)) if xs else ("", "", "")

        def avg(xs):
            return round(sum(xs) / len(xs), 1) if xs else ""
        row = [c["start"], *[round(x, 1) if x != "" else "" for x in stats(c["cpu"])],
               *[round(x, 1) if x != "" else "" for x in stats(c["gpu"])],
               avg(c["cl"]), avg(c["gl"]), avg(c["cw"]), avg(c["gw"])]
        try:
            new = not os.path.isfile(self.path)
            with open(self.path, "a", encoding="utf-8") as f:
                if new:
                    f.write(",".join(HIST_FIELDS) + "\n")
                f.write(",".join(str(x) for x in row) + "\n")
        except OSError:
            log("couldn't write the history file")

    def load(self, since=0):
        rows = []
        try:
            with open(self.path, encoding="utf-8") as f:
                head = f.readline().strip().split(",")
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) != len(head):
                        continue
                    d = {}
                    for k, v in zip(head, parts):
                        d[k] = float(v) if v not in ("", None) else None
                    if d.get("start") and d["start"] >= since:
                        rows.append(d)
        except (OSError, ValueError):
            pass
        return rows

    def trim(self, keep_days):
        rows = self.load()
        cutoff = time.time() - keep_days * 86400
        if rows and rows[0]["start"] < cutoff:
            keep = [r for r in rows if r["start"] >= cutoff]
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(",".join(HIST_FIELDS) + "\n")
                    for r in keep:
                        f.write(",".join("" if r.get(k) is None else str(int(r[k]) if k == "start" else r[k])
                                         for k in HIST_FIELDS) + "\n")
            except OSError:
                pass


# ======================================================================
# Cool-down: limit the CPU's top speed while it's hot
# ======================================================================

POWER_SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"


def powercfg(*args):
    """Run powercfg; returns its output (raises OSError on failure)."""
    if not IS_WIN:
        FAKE_ACTIONS.append(("powercfg",) + args)
        if args[:1] == ("/q",):
            return "Current AC Power Setting Index: 0x00000064\nCurrent DC Power Setting Index: 0x00000064\n"
        if args[:1] == ("/getactivescheme",):
            return "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)"
        return ""
    r = subprocess.run(["powercfg", *args], capture_output=True, text=True, timeout=15,
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise OSError((r.stdout + r.stderr).strip() or f"powercfg failed ({r.returncode})")
    return r.stdout


class CoolDown:
    def __init__(self):
        self.active = False
        self.since = None
        self.cool_since = None
        self.saved = None
        self.error = None

    def restore_leftover(self):
        """A cool-down that was still on when the monitor stopped: put the settings back."""
        try:
            with open(COOLDOWN_FILE, encoding="utf-8") as f:
                self.saved = json.load(f)
        except (OSError, ValueError):
            return
        log("cool-down: putting back the settings from last time")
        self.release(force=True)

    def update(self, now, levels, temps):
        """levels/temps: {"cpu": …, "gpu": …}. Returns "on", "off" or None (no change)."""
        if not SETTINGS.get("cd_on", True):
            return self.release() if self.active else None
        rank = {None: 0, "warning": 1, "critical": 2}
        need = 2 if SETTINGS.get("cd_trigger") == "critical" else 1
        watch = SETTINGS.get("cd_watch", {"cpu", "gpu"})
        hot = any(rank.get(levels.get(m)) >= need for m in watch)
        if hot and not self.active:
            return self.engage()
        if self.active:
            limits = {"cpu": SETTINGS.get("cpu_warn", 85), "gpu": SETTINGS.get("gpu_warn", 83)}
            cool = all(temps.get(m) is None or temps[m] < limits[m] - 5 for m in watch)
            if not cool:
                self.cool_since = None
            else:
                self.cool_since = self.cool_since or now
                if now - self.cool_since >= SETTINGS.get("cd_restore", 3) * 60:
                    return self.release()
        return None

    def engage(self):
        method = SETTINGS.get("cd_method", "max_cpu")
        try:
            if method == "power_saver":
                cur = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                                powercfg("/getactivescheme"), re.I)
                try:
                    powercfg("/setactive", POWER_SAVER)
                    self.saved = {"method": "power_saver", "scheme": cur.group(0) if cur else None}
                except OSError:
                    method = "max_cpu"  # this PC has no Power saver plan
            if method == "max_cpu":
                out = powercfg("/q", "SCHEME_CURRENT", "SUB_PROCESSOR", "PROCTHROTTLEMAX")
                vals = re.findall(r"0x([0-9a-f]{8})", out, re.I)
                if len(vals) < 2:
                    raise OSError("couldn't read the maximum processor state")
                ac, dc = int(vals[-2], 16), int(vals[-1], 16)  # the last two: current AC and DC values
                pct = str(SETTINGS.get("cd_max", 80))
                powercfg("/setacvalueindex", "SCHEME_CURRENT", "SUB_PROCESSOR", "PROCTHROTTLEMAX", pct)
                powercfg("/setdcvalueindex", "SCHEME_CURRENT", "SUB_PROCESSOR", "PROCTHROTTLEMAX", pct)
                powercfg("/setactive", "SCHEME_CURRENT")
                self.saved = {"method": "max_cpu", "ac": ac, "dc": dc}
        except (OSError, subprocess.SubprocessError) as e:
            self.error = str(e)
            log(f"cool-down failed: {e}")
            return None
        try:
            with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
                json.dump(self.saved, f)
        except OSError:
            pass
        self.active, self.since, self.cool_since, self.error = True, time.time(), None, None
        log(f"cool-down on: {self.describe()}")
        return "on"

    def release(self, force=False):
        if not self.active and not force:
            return None
        sv = self.saved or {}
        try:
            if sv.get("method") == "power_saver" and sv.get("scheme"):
                powercfg("/setactive", sv["scheme"])
            elif sv.get("method") == "max_cpu":
                powercfg("/setacvalueindex", "SCHEME_CURRENT", "SUB_PROCESSOR", "PROCTHROTTLEMAX", str(sv.get("ac", 100)))
                powercfg("/setdcvalueindex", "SCHEME_CURRENT", "SUB_PROCESSOR", "PROCTHROTTLEMAX", str(sv.get("dc", 100)))
                powercfg("/setactive", "SCHEME_CURRENT")
        except (OSError, subprocess.SubprocessError) as e:
            log(f"cool-down: couldn't put the settings back: {e}")
            self.error = str(e)
        try:
            os.remove(COOLDOWN_FILE)
        except OSError:
            pass
        self.active, self.saved, self.cool_since = False, None, None
        log("cool-down off")
        return "off"

    def describe(self):
        sv = self.saved or {}
        if sv.get("method") == "power_saver":
            return "Power saver plan"
        return f"CPU limited to {SETTINGS.get('cd_max', 80)} %"


# ======================================================================
# Phone alerts with a Telegram bot
# ======================================================================

class Telegram:
    API = "https://api.telegram.org/bot{token}/{method}"
    MIN_GAP = 600  # the same alert at most every 10 minutes

    def __init__(self):
        self.last = {}
        self.status = ""

    def configured(self):
        return bool(SETTINGS.get("tg_token"))

    def _call(self, method, data=None):
        url = self.API.format(token=SETTINGS["tg_token"], method=method)
        body = urllib.parse.urlencode(data).encode() if data else None
        with urllib.request.urlopen(url, data=body, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def find_chat_id(self):
        """The chat of the last message someone sent to the bot (you, after setting it up)."""
        res = self._call("getUpdates")
        chats = [u.get("message", {}).get("chat", {}).get("id") for u in res.get("result", [])]
        chats = [c for c in chats if c]
        return str(chats[-1]) if chats else None

    def send(self, text, key=None, done=None):
        """Send in the background. key: rate-limit the same alert. done(ok, message) is called after."""
        if not self.configured() or (key and time.time() - self.last.get(key, 0) < self.MIN_GAP):
            return False
        if key:
            self.last[key] = time.time()

        def work():
            ok, msg = False, ""
            try:
                chat = SETTINGS.get("tg_chat")
                if not chat:
                    chat = self.find_chat_id()
                    if not chat:
                        raise OSError("no chat yet — send any message to your bot in Telegram, then try again")
                    SETTINGS["tg_chat"] = chat
                    save_setting("telegram", "chat_id", chat)
                res = self._call("sendMessage", {"chat_id": chat, "text": text})
                ok = bool(res.get("ok"))
                msg = "sent ✓" if ok else str(res.get("description", "not sent"))
            except Exception as e:
                msg = str(getattr(e, "reason", e))
                log(f"telegram: {msg}")
            self.status = msg
            if done:
                done(ok, msg)
        threading.Thread(target=work, daemon=True).start()
        return True


def save_setting(section, key, value):
    """Change one value in system_monitor.ini, keeping the rest of the file as it is."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            text = f.read()
        m = re.search(rf"^\[{section}\]\s*$", text, re.M)
        if not m:
            text = text.rstrip("\n") + f"\n\n[{section}]\n{key} = {value}\n"
        else:
            nxt = re.search(r"^\[", text[m.end():], re.M)
            end = m.end() + nxt.start() if nxt else len(text)
            part = text[m.end():end]
            part2, n = re.subn(rf"^{key}\s*=.*$", f"{key} = {value}", part, count=1, flags=re.M)
            if not n:
                part2 = part.rstrip("\n") + f"\n{key} = {value}\n" + ("\n" if nxt else "")
            text = text[:m.end()] + part2 + text[end:]
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        log(f"couldn't save {key} in system_monitor.ini")


# ======================================================================
# Alarm: warning / critical, only after the value stays high for a while
# ======================================================================

class AlarmEngine:
    NAMES = {"cpu": "CPU", "gpu": "GPU", "ram": "RAM"}
    RANK = {None: 0, "warning": 1, "critical": 2}

    def __init__(self):
        self.warn_since, self.crit_since = {}, {}
        self.level = {}          # metric -> "warning" | "critical"
        self.snooze_until = 0.0

    def update(self, now, values, limits, hold):
        """values: {"cpu": °C, "gpu": °C, "ram": %} (None = unknown); limits: {metric: (warning, critical)}.
        Returns events: [("warning" | "critical" | "ok", metric, value)]."""
        events = []
        for m, v in values.items():
            warn, crit = limits.get(m, (0, 0))
            over_w = v is not None and bool(warn) and v >= warn
            over_c = v is not None and bool(crit) and v >= crit
            if over_w:
                self.warn_since.setdefault(m, now)
            else:
                self.warn_since.pop(m, None)
            if over_c:
                self.crit_since.setdefault(m, now)
            else:
                self.crit_since.pop(m, None)
            level = None
            if over_c and now - self.crit_since[m] >= min(hold, 5):
                level = "critical"
            elif over_w and now - self.warn_since[m] >= hold:
                level = "warning"
            prev = self.level.get(m)
            if level is None and prev and v is not None and warn and v > warn - 3:
                level = "warning"  # a few degrees of margin before it counts as back to normal
            if prev == "critical" and level == "warning" and over_w:
                level = "warning"
            if level == prev:
                continue
            if self.RANK[level] > self.RANK[prev]:
                if level == "warning" and now < self.snooze_until:
                    continue  # snoozed: fires when the snooze ends if it's still too high
                self.level[m] = level
                events.append((level, m, v))
            else:
                if level:
                    self.level[m] = level
                else:
                    self.level.pop(m, None)
                    events.append(("ok", m, v))
        return events

    def worst(self):
        return max(self.level.values(), key=lambda x: self.RANK[x]) if self.level else None


# ======================================================================
# Processes and system actions
# ======================================================================

FAKE_ACTIONS = []  # what would have run, when not on Windows (tests)


def sample_processes(gpu_by_pid=None):
    gpu_by_pid = gpu_by_pid or {}
    ncpu = psutil.cpu_count() or 1
    out = []
    for p in psutil.process_iter(["name", "memory_info"]):
        if p.pid == 0:
            continue
        try:
            cpu = p.cpu_percent(None) / ncpu
            mi = p.info.get("memory_info")
            out.append({"pid": p.pid, "name": p.info.get("name") or "?", "cpu": cpu, "mem": mi.rss if mi else 0,
                        "gpu": gpu_by_pid.get(p.pid)})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return out


def is_protected(name, pid):
    return pid in (0, 4, os.getpid()) or (name or "").lower() in SETTINGS.get("protected", set())


def end_process(pid, tree=False):
    """End a process (and its children). Returns a message for the status bar."""
    try:
        p = psutil.Process(pid)
        name = p.name()
    except psutil.NoSuchProcess:
        return "That process has already ended."
    if is_protected(name, pid):
        return f"{name} is protected (Windows needs it) — not ended."
    procs = [p]
    if tree:
        try:
            procs += [c for c in p.children(recursive=True) if not is_protected(c.name(), c.pid)]
        except psutil.Error:
            pass
    denied = 0
    for x in procs:
        try:
            x.terminate()
        except psutil.AccessDenied:
            denied += 1
        except psutil.NoSuchProcess:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=3)
    for x in alive:
        try:
            x.kill()
        except psutil.AccessDenied:
            denied += 1
        except psutil.NoSuchProcess:
            pass
    log(f"ended {name} (pid {pid}{', with its children' if tree else ''}); access denied: {denied}")
    if denied:
        return f"Couldn't end {name}: access denied — run System Monitor as administrator."
    extra = f" and {len(procs) - 1} child process{'es' if len(procs) > 2 else ''}" if tree and len(procs) > 1 else ""
    return f"Ended {name}{extra}."


def system_action(kind, seconds=0):
    """shutdown | restart | sleep | shutdown_later | cancel_shutdown"""
    log(f"system action: {kind} {seconds or ''}".strip())
    if not IS_WIN:
        FAKE_ACTIONS.append((kind, seconds))
        return
    flags = CREATE_NO_WINDOW
    if kind == "shutdown":
        subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=flags)
    elif kind == "restart":
        subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=flags)
    elif kind == "shutdown_later":
        subprocess.Popen(["shutdown", "/s", "/t", str(int(seconds)), "/c", "Scheduled by System Monitor"],
                         creationflags=flags)
    elif kind == "cancel_shutdown":
        subprocess.Popen(["shutdown", "/a"], creationflags=flags)
    elif kind == "sleep":
        ctypes.windll.powrprof.SetSuspendState(False, False, False)


def play_sound(critical=False):
    if not SETTINGS.get("sound", True) or not IS_WIN:
        return
    try:
        import winsound
        winsound.PlaySound("SystemHand" if critical else "SystemExclamation",
                           winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass


# ======================================================================
# UI
# ======================================================================

def apply_theme(root):
    root.configure(bg=T["bg"])
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    f = UI_FONT
    st.configure(".", background=T["bg"], foreground=T["fg"], fieldbackground=T["field"], font=(f, 10),
                 bordercolor=T["border"], lightcolor=T["border"], darkcolor=T["border"])
    st.configure("Treeview", background=T["surface"], fieldbackground=T["surface"], foreground=T["fg"],
                 rowheight=26, borderwidth=0)
    st.map("Treeview", background=[("selected", T["select"])], foreground=[("selected", T["fg"])])
    st.configure("Treeview.Heading", background=T["bg"], foreground=T["muted"], relief="flat", font=(f, 9, "bold"))
    st.map("Treeview.Heading", background=[("active", T["hover"])])
    st.configure("TButton", background=T["field"], foreground=T["fg"], borderwidth=0, padding=(10, 5))
    st.configure("TMenubutton", background=T["field"], foreground=T["fg"], borderwidth=0, padding=(10, 5),
                 arrowsize=0)
    st.map("TMenubutton", background=[("active", T["border"])])
    st.map("TButton", background=[("active", T["border"])])
    st.configure("Accent.TButton", background=T["accent"], foreground="#0b0d12", font=(f, 10, "bold"))
    st.map("Accent.TButton", background=[("active", "#a9bcff")])
    st.configure("Danger.TButton", background="#4a2328", foreground="#ffd7d7")
    st.map("Danger.TButton", background=[("active", "#6a2a31")])
    st.configure("TEntry", fieldbackground=T["field"], foreground=T["fg"], insertcolor=T["fg"], borderwidth=1)
    st.configure("Vertical.TScrollbar", background=T["surface"], troughcolor=T["bg"], borderwidth=0,
                 arrowcolor=T["muted"])


def temp_color(c, warn, crit):
    if c is None:
        return T["faint"]
    if crit and c >= crit:
        return T["crit"]
    if warn and c >= warn:
        return T["warn"]
    return T["ok"]


class Chart(tk.Canvas):
    """Usage (filled, 0-100 %) and temperature (orange line) over the last history_seconds.
    Click it to see power (W) and fan speed instead (when the sensors give them)."""

    def __init__(self, parent, height=110, toggle=True):
        super().__init__(parent, height=height, bg=T["surface"], highlightthickness=0, bd=0)
        self.points = []
        self.limit = None
        self.mode = "temp"
        self.bind("<Configure>", lambda e: self.redraw())
        if toggle:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda e: self.toggle())

    def toggle(self):
        self.mode = "power" if self.mode == "temp" else "temp"
        self.redraw()

    def set(self, points, limit=None):
        self.points, self.limit = points, limit
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        span = SETTINGS.get("history", 120)
        for frac in (0.25, 0.5, 0.75):
            y = h * frac
            self.create_line(0, y, w, y, fill=T["border"])
        if not self.points:
            return
        now = self.points[-1][0]

        def x_of(t):
            return w - (now - t) / span * w
        if self.mode == "power":
            self._draw_power(w, h, [(x_of(p[0]),) + tuple(p[3:5]) for p in self.points
                                    if now - p[0] <= span and len(p) >= 5])
            return
        pts = [(x_of(p[0]), p[1], p[2]) for p in self.points if now - p[0] <= span]
        usage = [(x, h - (u / 100) * (h - 4) - 2) for x, u, _c in pts if u is not None]
        if len(usage) >= 2:
            poly = [usage[0][0], h] + [v for p in usage for v in p] + [usage[-1][0], h]
            self.create_polygon(*poly, fill="#23305a", outline="")
            self.create_line(*[v for p in usage for v in p], fill=T["accent"], width=1.5)
        temps = [(x, c) for x, _u, c in pts if c is not None]
        lo, hi = 20, 110
        if self.limit:
            yl = h - (self.limit - lo) / (hi - lo) * h
            self.create_line(0, yl, w, yl, fill=T["warn"], dash=(3, 3))
        if len(temps) >= 2:
            line = [v for x, c in temps for v in (x, h - (min(hi, max(lo, c)) - lo) / (hi - lo) * h)]
            self.create_line(*line, fill=T["temp"], width=2)
        self.create_text(4, 3, text="100 %", anchor="nw", fill=T["faint"], font=(UI_FONT, 7))
        self.create_text(w - 4, 3, text=fmt_temp(hi), anchor="ne", fill=T["temp"], font=(UI_FONT, 7))

    def _draw_power(self, w, h, pts):
        """Power (yellow) and fan speed (green), each scaled to its own maximum."""
        power = [(x, p) for x, p, _f in pts if p is not None]
        fans = [(x, f) for x, _p, f in pts if f is not None]
        if not power and not fans:
            self.create_text(w / 2, h / 2, text="No power / fan sensors\n(LibreHardwareMonitor gives them)",
                             fill=T["faint"], font=(UI_FONT, 8), justify="center")
        for series, color, unit, x_anchor in ((power, "#f5d76e", "W", "nw"), (fans, T["ok"], "RPM", "ne")):
            if not series:
                continue
            top = max(v for _x, v in series) * 1.2 or 1
            if len(series) >= 2:
                self.create_line(*[c for x, v in series for c in (x, h - v / top * (h - 4) - 2)], fill=color, width=2)
            now_v = series[-1][1]
            self.create_text(4 if x_anchor == "nw" else w - 4, 3, anchor=x_anchor, fill=color, font=(UI_FONT, 7),
                             text=f"{now_v:.0f} {unit if unit != 'RPM' or now_v > 100 else '%'}")


class Card(tk.Frame):
    def __init__(self, parent, title):
        super().__init__(parent, bg=T["surface"], padx=14, pady=12, highlightthickness=1,
                         highlightbackground=T["border"])
        top = tk.Frame(self, bg=T["surface"])
        top.pack(fill="x")
        tk.Label(top, text=title, bg=T["surface"], fg=T["muted"], font=(UI_FONT, 9, "bold")).pack(side="left")
        self.sub = tk.Label(top, text="", bg=T["surface"], fg=T["faint"], font=(UI_FONT, 8))
        self.sub.pack(side="left", padx=(8, 0))
        row = tk.Frame(self, bg=T["surface"])
        row.pack(fill="x", pady=(6, 2))
        self.usage = tk.Label(row, text="—", bg=T["surface"], fg=T["fg"], font=(UI_FONT, 24, "bold"))
        self.usage.pack(side="left")
        self.temp = tk.Label(row, text="", bg=T["surface"], fg=T["faint"], font=(UI_FONT, 18, "bold"))
        self.temp.pack(side="right")
        self.details = tk.Label(self, text="", bg=T["surface"], fg=T["muted"], font=(UI_FONT, 9), justify="left",
                                anchor="w")
        self.details.pack(fill="x")
        self.bind("<Configure>", lambda e: self.details.configure(wraplength=max(120, e.width - 32)))
        self.chart = Chart(self, toggle=title != "MEMORY")
        if title != "MEMORY":
            tk.Label(self, text="click the chart: temperature ↔ power & fan", bg=T["surface"], fg=T["faint"],
                     font=(UI_FONT, 7)).pack(anchor="e")
        self.chart.pack(fill="both", expand=True, pady=(8, 0))


class AlarmPopup(tk.Toplevel):
    """Shown when it gets too hot. Doesn't take the focus (so a game or a video keeps it)."""

    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=T["crit"])
        self.card = tk.Frame(self, bg=T["bg"], padx=16, pady=14)
        self.card.pack(padx=2, pady=2, fill="both", expand=True)
        self._styled = False
        self.shown_at = 0
        self.test_until = 0

    def is_open(self):
        return self.state() == "normal"

    def _btn(self, parent, text, cmd, kind="normal"):
        bg = {"accent": T["accent"], "danger": "#4a2328", "normal": T["field"]}[kind]
        fg = {"accent": "#0b0d12", "danger": "#ffd7d7", "normal": T["fg"]}[kind]
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=(UI_FONT, 9, "bold" if kind != "normal" else "normal"),
                     padx=10, pady=5, cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        return b

    def render(self):
        app = self.app
        for w in self.card.winfo_children():
            w.destroy()
        levels, current = dict(app.alarm.level), dict(app.current)
        test = time.time() < self.test_until
        if test:
            levels.setdefault("cpu", "warning")
            if current.get("cpu") is None:
                current["cpu"] = SETTINGS["cpu_warn"]
        worst = max(levels.values(), key=lambda x: AlarmEngine.RANK[x]) if levels else None
        color = T["crit"] if worst == "critical" else T["warn"] if worst else T["ok"]
        self.configure(bg=color)
        f = UI_FONT
        lines = []
        for m, lvl in sorted(levels.items(), key=lambda kv: -AlarmEngine.RANK[kv[1]]):
            v = current.get(m)
            val = fmt_temp(v) if m != "ram" else (f"{v:.0f} %" if v is not None else "—")
            what = "is too hot" if m != "ram" else "is almost full"
            lines.append((f"{AlarmEngine.NAMES[m]} {what} — {val}", lvl))
        title = ("Test — " if test else "") + (lines[0][0] if lines else "Back to normal")
        tk.Label(self.card, text=("⚠  " if worst else "✓  ") + title, bg=T["bg"], fg=color,
                 font=(f, 13, "bold"), anchor="w").pack(fill="x")
        for text, lvl in lines[1:]:
            tk.Label(self.card, text=text, bg=T["bg"], fg=T["crit"] if lvl == "critical" else T["warn"],
                     font=(f, 10), anchor="w").pack(fill="x")
        tk.Label(self.card, text=("Critical" if worst == "critical" else "Warning" if worst else "Temperatures are fine again")
                 + f" · {time.strftime('%H:%M:%S')}", bg=T["bg"], fg=T["faint"], font=(f, 8), anchor="w").pack(fill="x")
        if worst:
            tk.Label(self.card, text="BUSIEST PROCESSES", bg=T["bg"], fg=T["muted"], font=(f, 8, "bold"),
                     anchor="w").pack(fill="x", pady=(10, 2))
            by_gpu = "gpu" in levels and "cpu" not in levels and any(p.get("gpu") for p in app.procs)
            for p in app.top_processes(3, "gpu" if by_gpu else "cpu"):
                row = tk.Frame(self.card, bg=T["bg"])
                row.pack(fill="x", pady=1)
                tk.Label(row, text=f"{p['name']}", bg=T["bg"], fg=T["fg"], font=(f, 10), anchor="w", width=26
                         ).pack(side="left")
                use = f"{p.get('gpu') or 0:.0f} % GPU" if by_gpu else f"{p['cpu']:.0f} % CPU"
                tk.Label(row, text=f"{use} · {fmt_bytes(p['mem'])}", bg=T["bg"], fg=T["muted"],
                         font=(f, 9), width=20, anchor="w").pack(side="left")
                if not is_protected(p["name"], p["pid"]):
                    self._btn(row, "End", lambda pid=p["pid"]: app.end_process(pid), "danger").pack(side="right")
        if app.cooldown.active:
            tk.Label(self.card, text=f"❄  Cool-down on: {app.cooldown.describe()} until it cools down",
                     bg=T["bg"], fg=T["accent"], font=(f, 9), anchor="w").pack(fill="x", pady=(8, 0))
        cd = app.countdown
        if cd:
            left = max(0, int(cd["end"] - time.time() + 0.99))
            box = tk.Frame(self.card, bg="#3a1d21", padx=10, pady=8)
            box.pack(fill="x", pady=(10, 0))
            what = {"shutdown": "Shutting down", "sleep": "Going to sleep",
                    "kill_top": f"Ending {cd.get('target', 'the busiest process')}"}[cd["action"]]
            tk.Label(box, text=f"{what} in {left} s", bg="#3a1d21", fg="#ffd7d7", font=(f, 11, "bold")
                     ).pack(side="left")
            self._btn(box, "Cancel", app.cancel_countdown, "accent").pack(side="right")
        row = tk.Frame(self.card, bg=T["bg"])
        row.pack(fill="x", pady=(12, 0))
        self._btn(row, "Dismiss", app.dismiss_alarm).pack(side="right", padx=(6, 0))
        if worst:
            self._btn(row, f"Snooze {SETTINGS.get('snooze', 10):.0f} min", app.snooze_alarm).pack(side="right",
                                                                                                padx=(6, 0))
            if not cd:
                self._btn(row, "Sleep", lambda: app.start_countdown("sleep", 10)).pack(side="left", padx=(0, 6))
                self._btn(row, f"Shut down in {SETTINGS.get('countdown', 60)} s",
                          lambda: app.start_countdown("shutdown"), "danger").pack(side="left")
        if not self.is_open():
            self.geometry("+-3000+-3000")
        if not self.is_open():
            self.deiconify()
            self.shown_at = time.time()
            if IS_WIN and not self._styled:
                try:  # clickable, but never takes the focus; not in Alt+Tab
                    hwnd = int(self.wm_frame(), 16)
                    u = ctypes.windll.user32
                    u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80)
                except Exception:
                    log("alarm style error:\n" + traceback.format_exc())
                self._styled = True
        self.lift()
        app.stack_popups()

    def hide(self):
        self.withdraw()
        self.app.stack_popups()



class RulePopup(tk.Toplevel):
    """Process-rule alerts: "chrome.exe — memory 4.2 GB for 2 min" with End / Ignore."""

    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=T["accent"])
        self.card = tk.Frame(self, bg=T["bg"], padx=16, pady=12)
        self.card.pack(padx=2, pady=2, fill="both", expand=True)
        self.items = []   # [(rule, proc, value, when, done_text)]
        self._styled = False

    def is_open(self):
        return self.state() == "normal"

    def add(self, rule, proc, value, done_text=None):
        self.items = [x for x in self.items if not (x[0]["key"] == rule["key"] and x[1]["pid"] == proc["pid"])]
        self.items.append((rule, proc, value, time.time(), done_text))
        self.items = self.items[-6:]
        self.render()

    def _btn(self, parent, text, cmd, kind="normal"):
        return AlarmPopup._btn(self, parent, text, cmd, kind)

    def render(self):
        for w in self.card.winfo_children():
            w.destroy()
        if not self.items:
            self.hide()
            return
        f = UI_FONT
        tk.Label(self.card, text="Process alert", bg=T["bg"], fg=T["accent"], font=(f, 12, "bold"), anchor="w"
                 ).pack(fill="x")
        for rule, proc, value, when, done in list(self.items):
            row = tk.Frame(self.card, bg=T["bg"])
            row.pack(fill="x", pady=(6, 0))
            mins = rule["seconds"] / 60
            dur = f"{mins:.0f} min" if mins >= 1 else f"{rule['seconds']:.0f} s"
            what = {"cpu": "CPU", "gpu": "GPU", "memory": "memory"}[rule["metric"]]
            tk.Label(row, text=f"{proc['name']}  ·  {what} {fmt_rule_value(rule, value)} for {dur}", bg=T["bg"],
                     fg=T["fg"], font=(f, 10), anchor="w").pack(side="left")
            if done:
                tk.Label(row, text=done, bg=T["bg"], fg=T["muted"], font=(f, 9)).pack(side="right")
            else:
                self._btn(row, "Ignore", lambda r=rule, p=proc: self.ignore(r, p)).pack(side="right", padx=(6, 0))
                if not is_protected(proc["name"], proc["pid"]):
                    self._btn(row, "End", lambda r=rule, p=proc: self.end(r, p), "danger").pack(side="right")
            tk.Label(self.card, text=f"rule “{rule['key']}”: {rule['text']} · {time.strftime('%H:%M', time.localtime(when))}",
                     bg=T["bg"], fg=T["faint"], font=(f, 8), anchor="w").pack(fill="x")
        row = tk.Frame(self.card, bg=T["bg"])
        row.pack(fill="x", pady=(10, 0))
        self._btn(row, "Close", self.close).pack(side="right")
        if not self.is_open():
            self.geometry("+-3000+-3000")
            self.deiconify()
            if IS_WIN and not self._styled:
                try:
                    hwnd = int(self.wm_frame(), 16)
                    u = ctypes.windll.user32
                    u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80)
                except Exception:
                    log("rule popup style error:\n" + traceback.format_exc())
                self._styled = True
        self.lift()
        self.app.stack_popups()

    def end(self, rule, proc):
        self.app.end_process(proc["pid"])
        self.items = [x for x in self.items if x[1]["pid"] != proc["pid"]]
        self.render()

    def ignore(self, rule, proc):
        self.app.rules.ignored.add((rule["key"], proc["pid"]))
        self.items = [x for x in self.items if not (x[0]["key"] == rule["key"] and x[1]["pid"] == proc["pid"])]
        self.render()

    def close(self):
        self.items = []
        self.hide()

    def hide(self):
        self.withdraw()
        self.app.stack_popups()


class HistoryWindow(tk.Toplevel):
    """Temperatures over the last day / week / month (min–max band and average line)."""
    RANGES = (("24 hours", 1), ("7 days", 7), ("30 days", 30))

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Temperature history")
        self.configure(bg=T["bg"])
        self.geometry("940x560")
        self.minsize(600, 380)
        self.days = 1
        f = UI_FONT
        top = tk.Frame(self, bg=T["bg"], padx=16, pady=12)
        top.pack(fill="x")
        tk.Label(top, text="Temperature history", bg=T["bg"], fg=T["fg"], font=(f, 14, "bold")).pack(side="left")
        self.btns = {}
        for label, days in reversed(self.RANGES):
            b = ttk.Button(top, text=label, command=lambda d=days: self.show(d))
            b.pack(side="right", padx=(6, 0))
            self.btns[days] = b
        self.summary = tk.Label(self, text="", bg=T["bg"], fg=T["muted"], font=(f, 10), anchor="w", justify="left",
                                padx=16)
        self.summary.pack(fill="x")
        self.cv = tk.Canvas(self, bg=T["surface"], highlightthickness=0)
        self.cv.pack(fill="both", expand=True, padx=16, pady=12)
        self.hover = tk.Label(self, text="", bg=T["bg"], fg=T["fg"], font=(f, 9), anchor="w", padx=16)
        self.hover.pack(fill="x", pady=(0, 10))
        self.cv.bind("<Configure>", lambda e: self.draw())
        self.cv.bind("<Motion>", self._motion)
        self.rows = []
        self.show(1)

    def show(self, days):
        self.days = days
        for d, b in self.btns.items():
            b.configure(style="Accent.TButton" if d == days else "TButton")
        rows = self.app.history.load(time.time() - days * 86400)
        if self.app.history.cur:  # the 10 minutes in progress
            c = self.app.history.cur
            rows.append({"start": c["start"], "cpu_avg": sum(c["cpu"]) / len(c["cpu"]) if c["cpu"] else None,
                         "cpu_min": min(c["cpu"]) if c["cpu"] else None, "cpu_max": max(c["cpu"]) if c["cpu"] else None,
                         "gpu_avg": sum(c["gpu"]) / len(c["gpu"]) if c["gpu"] else None,
                         "gpu_min": min(c["gpu"]) if c["gpu"] else None, "gpu_max": max(c["gpu"]) if c["gpu"] else None,
                         "cpu_load": None, "gpu_load": None})
        # 7 / 30 days: group into 1 h / 4 h blocks so the chart stays readable
        step = {1: 600, 7: 3600, 30: 4 * 3600}[days]
        groups = {}
        for r in rows:
            groups.setdefault(int(r["start"] // step * step), []).append(r)
        self.rows = []
        for start in sorted(groups):
            g = groups[start]

            def agg(key, fn):
                vals = [r[key] for r in g if r.get(key) is not None]
                return fn(vals) if vals else None
            self.rows.append({"start": start,
                              **{f"{d}_min": agg(f"{d}_min", min) for d in ("cpu", "gpu")},
                              **{f"{d}_max": agg(f"{d}_max", max) for d in ("cpu", "gpu")},
                              **{f"{d}_avg": agg(f"{d}_avg", lambda v: sum(v) / len(v)) for d in ("cpu", "gpu")}})
        self._summary(rows)
        self.draw()

    def _summary(self, rows):
        parts = []
        for d, name in (("cpu", "CPU"), ("gpu", "GPU")):
            mx = [(r[f"{d}_max"], r["start"]) for r in rows if r.get(f"{d}_max") is not None]
            av = [r[f"{d}_avg"] for r in rows if r.get(f"{d}_avg") is not None]
            if not mx:
                parts.append(f"{name}: no data yet")
                continue
            peak, when = max(mx)
            parts.append(f"{name}: highest {fmt_temp(peak)} ({time.strftime('%a %d %b %H:%M', time.localtime(when))})"
                         f"  ·  average {fmt_temp(sum(av) / len(av))}")
        self.summary.configure(text="     ".join(parts) + ("" if rows else "   — the history fills up every 10 minutes"))

    def _x(self, t, w):
        span = self.days * 86400
        return 40 + (t - (time.time() - span)) / span * (w - 50)

    def _y(self, c, h):
        lo, hi = 20, 110
        return h - 20 - (min(hi, max(lo, c)) - lo) / (hi - lo) * (h - 30)

    def draw(self):
        cv = self.cv
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50 or h < 50:
            return
        for c in (30, 50, 70, 90, 110):
            y = self._y(c, h)
            cv.create_line(40, y, w - 10, y, fill=T["border"])
            cv.create_text(34, y, text=fmt_temp(c).replace(" ", ""), anchor="e", fill=T["faint"], font=(UI_FONT, 8))
        span = self.days * 86400
        ticks = 6 if self.days == 1 else self.days if self.days == 7 else 10
        for i in range(ticks + 1):
            t = time.time() - span + span * i / ticks
            x = self._x(t, w)
            cv.create_text(x, h - 8, text=time.strftime("%H:%M" if self.days == 1 else "%a %d", time.localtime(t)),
                           fill=T["faint"], font=(UI_FONT, 8))
        for limit, color in ((SETTINGS.get("cpu_warn"), T["temp"]), (SETTINGS.get("gpu_warn"), T["accent"])):
            if limit:
                y = self._y(limit, h)
                cv.create_line(40, y, w - 10, y, fill=color, dash=(3, 4))
        for d, color, band in (("cpu", T["temp"], "#4a3322"), ("gpu", T["accent"], "#253055")):
            pts = [r for r in self.rows if r.get(f"{d}_avg") is not None]
            if not pts:
                continue
            if len(pts) >= 2:
                top = [(self._x(r["start"], w), self._y(r[f"{d}_max"], h)) for r in pts]
                bottom = [(self._x(r["start"], w), self._y(r[f"{d}_min"], h)) for r in reversed(pts)]
                cv.create_polygon(*[v for p in top + bottom for v in p], fill=band, outline="", stipple="gray50")
                cv.create_line(*[v for r in pts for v in (self._x(r["start"], w), self._y(r[f"{d}_avg"], h))],
                               fill=color, width=2)
            else:
                x, y = self._x(pts[0]["start"], w), self._y(pts[0][f"{d}_avg"], h)
                cv.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
        cv.create_text(48, 10, text="CPU", anchor="nw", fill=T["temp"], font=(UI_FONT, 9, "bold"))
        cv.create_text(84, 10, text="GPU", anchor="nw", fill=T["accent"], font=(UI_FONT, 9, "bold"))
        cv.create_text(120, 10, text="line = average, band = lowest–highest, dashed = warning", anchor="nw",
                       fill=T["faint"], font=(UI_FONT, 8))

    def _motion(self, e):
        if not self.rows:
            return
        w = self.cv.winfo_width()
        r = min(self.rows, key=lambda r: abs(self._x(r["start"], w) - e.x))
        when = time.strftime("%a %d %b %H:%M", time.localtime(r["start"]))
        parts = [when]
        for d in ("cpu", "gpu"):
            if r.get(f"{d}_avg") is not None:
                parts.append(f"{d.upper()} {fmt_temp(r[f'{d}_avg'])} (lowest {fmt_temp(r[f'{d}_min'])}, "
                             f"highest {fmt_temp(r[f'{d}_max'])})")
        self.hover.configure(text="   ·   ".join(parts))


class Overlay(tk.Toplevel):
    """CPU / GPU usage and temperature, always on top in a corner. Never takes the focus.
    Double-click: open System Monitor. Right-click: move to another corner, hide."""

    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=T["border"])
        self.box = tk.Frame(self, bg=T["bg"], padx=10, pady=4)
        self.box.pack(padx=1, pady=1)
        f = UI_FONT
        self.parts = {}
        for key in ("cpu", "gpu", "ram"):
            fr = tk.Frame(self.box, bg=T["bg"])
            name = tk.Label(fr, text=key.upper(), bg=T["bg"], fg=T["muted"], font=(f, 8, "bold"))
            name.pack(side="left", padx=(0, 5))
            usage = tk.Label(fr, text="—", bg=T["bg"], fg=T["fg"], font=(f, 10, "bold"), width=4, anchor="e")
            usage.pack(side="left")
            temp = tk.Label(fr, text="", bg=T["bg"], fg=T["faint"], font=(f, 10, "bold"), width=5, anchor="e")
            temp.pack(side="left")
            self.parts[key] = (fr, usage, temp)
        self.seps = [tk.Frame(self.box, bg=T["border"], width=1, height=14) for _ in range(2)]
        for w in [self.box, self] + [x for p in self.parts.values() for x in p]:
            w.bind("<Double-Button-1>", lambda e: app.show_main())
            w.bind("<Button-3>", self._menu)
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<ButtonRelease-1>", self._drag_end)
        self._styled_through = None
        self._hwnd = None
        self._hidden = False   # "Hide (until restart)"
        self._drag_from = None
        self._last_lift = 0
        self.apply_settings()

    def apply_settings(self):
        if not SETTINGS.get("ov_on", True):
            self.withdraw()
            return
        alpha = SETTINGS.get("ov_alpha", 0.9)
        if SETTINGS.get("ov_through") and alpha >= 1:
            alpha = 0.99  # click-through needs a layered window, which Tk makes when opacity < 100 %
        keys = ["cpu", "gpu"] + (["ram"] if SETTINGS.get("ov_ram") else [])
        for w in self.box.winfo_children():
            w.pack_forget()
        for i, k in enumerate(keys):
            if i:
                self.seps[min(i - 1, 1)].pack(side="left", padx=9, pady=2)
            self.parts[k][0].pack(side="left")
        self.parts["ram"][2].configure(text="")
        self.deiconify()
        self.update_idletasks()
        try:
            self.attributes("-alpha", alpha)  # after it's on screen: Tk sets the layered style + opacity itself
        except tk.TclError:
            pass
        self._style()
        self.place_corner()

    def _style(self):
        if not IS_WIN:
            return
        through = SETTINGS.get("ov_through", False)
        try:
            hwnd = int(self.wm_frame(), 16)
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80  # no-activate, tool window (not in Alt+Tab)
            ex = ex | 0x20 if through else ex & ~0x20              # WS_EX_TRANSPARENT: click-through
            u.SetWindowLongW(hwnd, -20, ex)
            self._hwnd = hwnd
            self._styled_through = through
        except Exception:
            log("overlay style error:\n" + traceback.format_exc())

    def place_corner(self):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        l, t, r, b = self.app.work_area()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if r - l < w + 16 or b - t < h + 16 or r > sw * 3 or b > sh * 3:  # odd work area: use the screen
            l, t, r, b = 0, 0, sw, sh
        c = SETTINGS.get("ov_corner", "bottom-right")
        x = r - w - 8 if c.endswith("right") else l + 8
        y = b - h - 8 if c.startswith("bottom") else t + 8
        self.geometry(f"{w}x{h}+{x}+{y}")
        if IS_WIN and getattr(self, "_hwnd", None):
            try:  # on top, shown, without taking the focus; re-apply the style
                ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(self._hwnd), ctypes.c_void_p(-1), x, y, w, h,
                                                  0x0010 | 0x0040 | 0x0020)  # NOACTIVATE | SHOWWINDOW | FRAMECHANGED
            except Exception:
                log("overlay position error:\n" + traceback.format_exc())
        self.update_idletasks()
        log(f"overlay: {w}x{h} at {x},{y} (corner {c}; work area {l},{t},{r},{b}; screen {sw}x{sh}; "
            f"state {self.state()}; opacity {SETTINGS.get('ov_alpha')})")

    def height_used(self):
        """Pixels the overlay takes at the bottom-right (so the alarm pop-up goes above it)."""
        if self.state() != "normal" or SETTINGS.get("ov_corner") != "bottom-right":
            return 0
        return self.winfo_height() + 8

    def show_values(self, cpu_load, cpu_temp, gpu_load, gpu_temp, ram):
        if not SETTINGS.get("ov_on", True):
            return
        for key, load, temp, warn, crit in (("cpu", cpu_load, cpu_temp, SETTINGS["cpu_warn"], SETTINGS["cpu_crit"]),
                                            ("gpu", gpu_load, gpu_temp, SETTINGS["gpu_warn"], SETTINGS["gpu_crit"]),
                                            ("ram", ram, None, SETTINGS["ram_warn"], SETTINGS["ram_crit"])):
            _fr, u, t = self.parts[key]
            u.configure(text=f"{load:.0f}%" if load is not None else "—",
                        fg=temp_color(load, warn, crit) if key == "ram" and warn else T["fg"])
            if key != "ram":
                t.configure(text=fmt_temp(temp).replace(" ", "") if temp is not None else "", fg=temp_color(temp, warn,
                                                                                                         crit))
        alarm = self.app.alarm.worst()
        self.configure(bg=T["crit"] if alarm == "critical" else T["warn"] if alarm else T["border"])
        cool = "❄" if self.app.cooldown.active else ""
        if self.parts["cpu"][0].winfo_children()[0].cget("text") != "CPU" + (" " + cool if cool else ""):
            self.parts["cpu"][0].winfo_children()[0].configure(text="CPU" + (" " + cool if cool else ""),
                                                               fg=T["accent"] if cool else T["muted"])
        if self._drag_from is None:
            w = self.winfo_reqwidth()
            if abs(w - self.winfo_width()) > 2:
                self.place_corner()
        if time.time() - self._last_lift > 3:  # stay visible, above windows that also want to be on top
            self._last_lift = time.time()
            if self.state() != "normal" and not self._hidden:
                self.deiconify()
                self._style()
                self.place_corner()
            self.lift()
            self.attributes("-topmost", True)

    # ---- mouse
    def _drag_start(self, e):
        self._grab = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())
        self._moved = False

    def _drag(self, e):
        if getattr(self, "_grab", None):
            self._moved = True
            self._drag_from = self._grab  # dragged: stays where you put it (until "Back to its corner")
            self.geometry(f"+{e.x_root - self._grab[0]}+{e.y_root - self._grab[1]}")

    def _drag_end(self, _e):
        self._grab = None

    def _menu(self, e):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Open System Monitor", command=self.app.show_main)
        corners = tk.Menu(m, tearoff=0)
        for c in ("bottom-right", "bottom-left", "top-right", "top-left"):
            corners.add_command(label=("✓ " if SETTINGS.get("ov_corner") == c else "    ") + c.replace("-", " ").capitalize(),
                                command=lambda c=c: self.app.set_overlay_corner(c))
        m.add_cascade(label="Move to", menu=corners)
        m.add_command(label="Back to its corner", command=lambda: (setattr(self, "_drag_from", None), self.place_corner()))
        m.add_separator()
        m.add_command(label="Hide (until restart)", command=lambda: (setattr(self, "_hidden", True), self.withdraw()))
        m.tk_popup(e.x_root, e.y_root)


class MonitorApp(tk.Tk):
    def __init__(self, minimized=False):
        super().__init__()
        self.title(TITLE)
        set_window_icon(self)
        apply_theme(self)
        self.geometry("1080x760")
        self.minsize(760, 560)
        self._quitting = False
        self.sensors = Sensors()
        self.alarm = AlarmEngine()
        self.current = {"cpu": None, "gpu": None, "ram": None}
        self.hist = {"cpu": [], "gpu": [], "ram": []}
        self.procs = []
        self.countdown = None    # {"action", "end", "target"}
        self._last_sound = 0
        self.sort_key, self.sort_desc = "cpu", True
        self.q = queue.Queue()
        self.settings_mtime = self._mtime()
        self.rules = RuleEngine()
        self.history = HistoryStore()
        self.cooldown = CoolDown()
        self.telegram = Telegram()
        self.hist_win = None
        self.cooldown.restore_leftover()
        if SETTINGS.get("hist_on", True):
            threading.Thread(target=lambda: self.history.trim(SETTINGS.get("hist_days", 90)), daemon=True).start()
        self.popup = AlarmPopup(self)
        self.rule_popup = RulePopup(self)
        self._build()
        self.overlay = Overlay(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if minimized:
            self.iconify()
        self.sensors.start()
        threading.Thread(target=self._sampler, daemon=True).start()
        self.after(100, self._poll)
        self.after(1000, self._tick)
        self.after(2000, self._check_settings)
        if SETTINGS_WARNINGS:
            self.status("system_monitor.ini: " + "; ".join(SETTINGS_WARNINGS), 15)

    # ---- layout
    def _build(self):
        f = UI_FONT
        head = tk.Frame(self, bg=T["bg"], padx=16, pady=12)
        head.pack(fill="x")
        tk.Label(head, text="System Monitor", bg=T["bg"], fg=T["fg"], font=(f, 15, "bold")).pack(side="left")
        self.sources_lbl = tk.Label(head, text="", bg=T["bg"], fg=T["faint"], font=(f, 8))
        self.sources_lbl.pack(side="right")
        cards = tk.Frame(self, bg=T["bg"], padx=16)
        cards.pack(fill="x")
        self.cards = {}
        for i, (key, title) in enumerate((("cpu", "CPU"), ("gpu", "GPU"), ("ram", "MEMORY"))):
            c = Card(cards, title)
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
            cards.columnconfigure(i, weight=1, uniform="card")
            self.cards[key] = c

        # processes
        bar = tk.Frame(self, bg=T["bg"], padx=16, pady=10)
        bar.pack(fill="x")
        tk.Label(bar, text="PROCESSES", bg=T["bg"], fg=T["muted"], font=(f, 9, "bold")).pack(side="left")
        self.filter = tk.StringVar()
        self.filter.trace_add("write", lambda *_: self._fill_procs())
        e = ttk.Entry(bar, textvariable=self.filter, width=24)
        e.pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="End process tree", style="Danger.TButton",
                   command=lambda: self.end_selected(tree=True)).pack(side="right")
        ttk.Button(bar, text="End process", style="Danger.TButton",
                   command=self.end_selected).pack(side="right", padx=(0, 6))
        table = tk.Frame(self, bg=T["bg"], padx=16)
        table.pack(fill="both", expand=True)
        cols = (("name", "Name", 300, "w"), ("pid", "PID", 80, "e"), ("cpu", "CPU", 90, "e"),
                ("gpu", "GPU", 80, "e"), ("mem", "Memory", 110, "e"))
        self.tree = ttk.Treeview(table, columns=[c[0] for c in cols], show="headings", selectmode="browse")
        for key, label, width, anchor in cols:
            self.tree.heading(key, text=label, command=lambda k=key: self._sort(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=key == "name")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("hot", foreground=T["warn"])
        self.tree.tag_configure("protected", foreground=T["faint"])
        self.tree.bind("<Button-3>", self._menu)
        self.tree.bind("<Delete>", lambda e: self.end_selected())

        # actions
        foot = tk.Frame(self, bg=T["bg"], padx=16, pady=12)
        foot.pack(fill="x")
        ttk.Button(foot, text="Shut down…", style="Danger.TButton",
                   command=lambda: self.confirm_action("shutdown")).pack(side="left")
        ttk.Button(foot, text="Restart…", command=lambda: self.confirm_action("restart")).pack(side="left", padx=6)
        ttk.Button(foot, text="Sleep", command=lambda: self.confirm_action("sleep")).pack(side="left")
        later = ttk.Menubutton(foot, text="Shut down later ▾")
        lm = tk.Menu(later, tearoff=0)
        lm.add_command(label="Shut down in… (minutes)", command=self.shutdown_later)
        lm.add_command(label="Cancel scheduled shutdown", command=self.cancel_shutdown)
        later["menu"] = lm
        later.pack(side="left", padx=6)
        ttk.Button(foot, text="Quit", command=self.quit_app).pack(side="right")
        ttk.Button(foot, text="Settings", command=self.open_settings).pack(side="right", padx=(0, 6))
        ttk.Button(foot, text="Test alarm", command=self.test_alarm).pack(side="right", padx=6)
        ttk.Button(foot, text="History", command=self.open_history).pack(side="right")
        self.status_lbl = tk.Label(self, text="", bg=T["surface"], fg=T["muted"], font=(f, 9), anchor="w",
                                   padx=16, pady=5)
        self.status_lbl.pack(fill="x", side="bottom")
        self._status_job = None

    def status(self, text, seconds=6):
        self.status_lbl.configure(text=text)
        if self._status_job:
            self.after_cancel(self._status_job)
        self._status_job = self.after(int(seconds * 1000), lambda: self.status_lbl.configure(text=""))

    def work_area(self):
        if IS_WIN:
            try:
                class RECT(ctypes.Structure):
                    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long),
                                ("b", ctypes.c_long)]
                r = RECT()
                ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
                return r.l, r.t, r.r, r.b
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    # ---- sampling (background thread) → queue → UI
    def _sampler(self):
        psutil.cpu_percent(None)
        n = 0
        while not self._quitting:
            t0 = time.time()
            try:
                snap = {"t": t0, "cpu": psutil.cpu_percent(None), "ram": psutil.virtual_memory(),
                        "cores": psutil.cpu_count(logical=True), "sensors": self.sensors.latest()}
                try:
                    fr = psutil.cpu_freq()
                    snap["freq"] = fr.current if fr else None
                except Exception:
                    snap["freq"] = None
                if n % 2 == 0:
                    snap["procs"] = sample_processes(self.sensors.proc_gpu)
                self.q.put(snap)
            except Exception:
                log("sample error:\n" + traceback.format_exc())
            n += 1
            time.sleep(max(0.05, SETTINGS.get("refresh_ms", 1000) / 1000 - (time.time() - t0)))

    def _poll(self):
        if self._quitting:
            return
        try:
            snap = None
            while True:
                try:
                    snap = self.q.get_nowait()
                    self._apply(snap)
                except queue.Empty:
                    break
        except Exception:
            log("ui error:\n" + traceback.format_exc())
        finally:
            if not self._quitting:
                self.after(100, self._poll)

    def _apply(self, snap):
        now = snap["t"]
        s = snap["sensors"]
        cpu = s["cpu"]
        gpus = s["gpus"]
        g = gpus[0] if gpus else None
        ram = snap["ram"]
        cpu_load = snap["cpu"]
        gpu_temp = max((x["temp"] for x in gpus if x.get("temp") is not None), default=None)
        self.current = {"cpu": cpu["temp"], "gpu": gpu_temp, "ram": ram.percent}
        span = SETTINGS.get("history", 120)
        gpu_fan = g.get("fan_value") if g else None
        for key, point in (("cpu", (now, cpu_load, cpu["temp"], cpu.get("power"), cpu.get("fan_rpm"))),
                           ("gpu", (now, g.get("load") if g else None, g.get("temp") if g else None,
                                    g.get("power") if g else None, gpu_fan)),
                           ("ram", (now, ram.percent, None))):
            h = self.hist[key]
            h.append(point)
            while h and now - h[0][0] > span + 5:
                h.pop(0)
        if SETTINGS.get("hist_on", True):
            self.history.add(now, cpu["temp"], gpu_temp, cpu_load, g.get("load") if g else None, cpu.get("power"),
                             g.get("power") if g else None)
        if "procs" in snap:
            self.procs = snap["procs"]
            self._fill_procs()
            for rule, proc, value in self.rules.update(now, self.procs, SETTINGS.get("rules", []), ram.total):
                self._on_rule(rule, proc, value)
        self._render_cards(snap, cpu, gpus, cpu_load)
        try:
            self.overlay.show_values(cpu_load, cpu["temp"], g.get("load") if g else None, gpu_temp, ram.percent)
        except tk.TclError:
            pass
        events = self.alarm.update(now, self.current,
                                   {"cpu": (SETTINGS["cpu_warn"], SETTINGS["cpu_crit"]),
                                    "gpu": (SETTINGS["gpu_warn"], SETTINGS["gpu_crit"]),
                                    "ram": (SETTINGS["ram_warn"], SETTINGS["ram_crit"])}, SETTINGS["hold"])
        for ev in events:
            self._on_alarm_event(*ev)
        change = self.cooldown.update(now, self.alarm.level, self.current)
        if change:
            self._on_cooldown(change)

    def _render_cards(self, snap, cpu, gpus, cpu_load):
        c = self.cards["cpu"]
        c.sub.configure(text=(cpu.get("name") or "")[:40])
        c.usage.configure(text=f"{cpu_load:.0f} %")
        c.temp.configure(text=fmt_temp(cpu["temp"]) if cpu["temp"] is not None else "",
                         fg=temp_color(cpu["temp"], SETTINGS["cpu_warn"], SETTINGS["cpu_crit"]))
        det = [f"{snap['cores']} threads" + (f" · {snap['freq'] / 1000:.2f} GHz" if snap.get("freq") else "")]
        if cpu.get("power"):
            det[0] += f" · {cpu['power']:.0f} W"
        if cpu.get("fan_rpm"):
            det[0] += f" · fan {cpu['fan_rpm']:.0f} RPM"
        if self.cooldown.active:
            det.append(f"❄ Cool-down: {self.cooldown.describe()}")
        det.append(f"Temperature: {cpu['temp_source']}" if cpu["temp"] is not None else
                   "No temperature — start LibreHardwareMonitor (see README)")
        c.details.configure(text="\n".join(det))
        c.chart.set(self.hist["cpu"], SETTINGS["cpu_warn"])

        c = self.cards["gpu"]
        if gpus:
            g = gpus[0]
            c.sub.configure(text=g["name"][:40])
            c.usage.configure(text=f"{g['load']:.0f} %" if g.get("load") is not None else "—")
            c.temp.configure(text=fmt_temp(g.get("temp")) if g.get("temp") is not None else "",
                             fg=temp_color(g.get("temp"), SETTINGS["gpu_warn"], SETTINGS["gpu_crit"]))
            det = []
            if g.get("mem_total"):
                det.append(f"VRAM {fmt_bytes(g.get('mem_used'))} / {fmt_bytes(g['mem_total'])}")
            extra = [x for x in (f"hot spot {fmt_temp(g['hotspot'])}" if g.get("hotspot") else None,
                                 f"{g['power']:.0f} W" if g.get("power") else None,
                                 f"fan {g['fan']}" if g.get("fan") else None) if x]
            if extra:
                det.append(" · ".join(extra))
            if g.get("temp") is None:
                det.append("No temperature — NVIDIA: nvidia-smi; others: LibreHardwareMonitor")
            for o in gpus[1:3]:
                det.append(f"{o['name'][:28]}: " + (f"{o['load']:.0f} %" if o.get("load") is not None else "—")
                           + (f" · {fmt_temp(o['temp'])}" if o.get("temp") is not None else ""))
            c.details.configure(text="\n".join(det))
        else:
            c.sub.configure(text="")
            c.usage.configure(text="—")
            c.temp.configure(text="")
            c.details.configure(text="No GPU information — see README (LibreHardwareMonitor / nvidia-smi)")
        c.chart.set(self.hist["gpu"], SETTINGS["gpu_warn"])

        c = self.cards["ram"]
        ram = snap["ram"]
        c.usage.configure(text=f"{ram.percent:.0f} %",
                          fg=temp_color(ram.percent, SETTINGS["ram_warn"], SETTINGS["ram_crit"])
                          if SETTINGS["ram_warn"] else T["fg"])
        c.sub.configure(text=f"{fmt_bytes(ram.total)}")
        c.details.configure(text=f"Used {fmt_bytes(ram.total - ram.available)} · free {fmt_bytes(ram.available)}")
        c.chart.set(self.hist["ram"])

        src = self.sensors.sources()
        self.sources_lbl.configure(text="   ".join(f"{k}: {v}" for k, v in src.items()))

    # ---- processes
    def top_processes(self, n=3, by="cpu"):
        return sorted(self.procs, key=lambda p: -(p.get(by) or 0))[:n]

    def _sort(self, key):
        self.sort_desc = (not self.sort_desc) if key == self.sort_key else key in ("cpu", "gpu", "mem")
        self.sort_key = key
        self._fill_procs()

    def _fill_procs(self):
        sel = self.tree.selection()
        sel_pid = int(sel[0]) if sel else None
        q = self.filter.get().strip().lower()
        rows = [p for p in self.procs if not q or q in p["name"].lower() or q == str(p["pid"])]
        k = self.sort_key
        rows.sort(key=(lambda p: p["name"].lower()) if k == "name" else (lambda p: p.get(k) or 0),
                  reverse=self.sort_desc)
        if not q:
            rows = rows[:SETTINGS.get("show", 40)]
        top = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        for p in rows:
            tags = ("protected",) if is_protected(p["name"], p["pid"]) else ("hot",) if p["cpu"] >= 50 else ()
            self.tree.insert("", "end", iid=str(p["pid"]), tags=tags,
                             values=(p["name"], p["pid"], f"{p['cpu']:.1f} %",
                                     f"{p['gpu']:.0f} %" if p.get("gpu") else "", fmt_bytes(p["mem"])))
        for key, label in (("name", "Name"), ("pid", "PID"), ("cpu", "CPU"), ("gpu", "GPU"), ("mem", "Memory")):
            arrow = (" ▼" if self.sort_desc else " ▲") if key == self.sort_key else ""
            self.tree.heading(key, text=label + arrow)
        if sel_pid is not None and self.tree.exists(str(sel_pid)):
            self.tree.selection_set(str(sel_pid))
        self.tree.yview_moveto(top)

    def _selected_proc(self):
        sel = self.tree.selection()
        if not sel:
            return None
        pid = int(sel[0])
        return next((p for p in self.procs if p["pid"] == pid), {"pid": pid, "name": "?"})

    def _menu(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        p = self._selected_proc()
        m = tk.Menu(self, tearoff=0)
        prot = is_protected(p["name"], p["pid"])
        m.add_command(label="End process", state="disabled" if prot else "normal", command=self.end_selected)
        m.add_command(label="End process tree", state="disabled" if prot else "normal",
                      command=lambda: self.end_selected(tree=True))
        m.add_separator()
        m.add_command(label="Open file location", command=lambda: self.open_location(p["pid"]))
        m.add_command(label="Copy name", command=lambda: (self.clipboard_clear(), self.clipboard_append(p["name"])))
        m.tk_popup(e.x_root, e.y_root)

    def end_selected(self, tree=False):
        p = self._selected_proc()
        if not p:
            self.status("Pick a process in the list first.")
            return
        if is_protected(p["name"], p["pid"]):
            self.status(f"{p['name']} is protected (Windows needs it) — not ended.")
            return
        what = "and everything it started " if tree else ""
        if not messagebox.askyesno("End process", f"End {p['name']} (PID {p['pid']}) {what}now?\n\n"
                                   "Unsaved work in it is lost.", icon="warning", parent=self):
            return
        self.end_process(p["pid"], tree)

    def end_process(self, pid, tree=False):
        msg = end_process(pid, tree)
        self.status(msg, 8)
        if self.popup.is_open():
            self.after(400, self.popup.render)

    def open_location(self, pid):
        try:
            exe = psutil.Process(pid).exe()
        except psutil.Error as e:
            self.status(f"Couldn't find its file ({type(e).__name__}).")
            return
        if IS_WIN:
            subprocess.Popen(["explorer.exe", "/select,", exe])
        else:
            self.status(exe)

    # ---- system actions
    def confirm_action(self, kind):
        name = {"shutdown": "Shut down", "restart": "Restart", "sleep": "Sleep"}[kind]
        if kind != "sleep" and not messagebox.askyesno(name, f"{name} now? Unsaved work in other apps may be lost.",
                                                       icon="warning", parent=self):
            return
        system_action(kind)

    def shutdown_later(self):
        minutes = simpledialog.askinteger("Shut down later", "Shut down in how many minutes?", parent=self,
                                          minvalue=1, maxvalue=24 * 60, initialvalue=30)
        if minutes:
            system_action("shutdown_later", minutes * 60)
            self.status(f"Windows will shut down at {time.strftime('%H:%M', time.localtime(time.time() + minutes * 60))}"
                        " — “Cancel shutdown” stops it.", 12)

    def cancel_shutdown(self):
        system_action("cancel_shutdown")
        self.cancel_countdown()
        self.status("Scheduled shutdown cancelled.")

    # ---- pop-ups in the bottom-right corner: alarm, then process alerts, above the overlay
    def stack_popups(self):
        l, t, r, b = self.work_area()
        y = b - 16 - (self.overlay.height_used() if getattr(self, "overlay", None) else 0)
        for pop in (getattr(self, "popup", None), getattr(self, "rule_popup", None)):
            if pop is None or not pop.is_open():
                continue
            pop.update_idletasks()
            w, h = max(420, pop.winfo_reqwidth()), pop.winfo_reqheight()
            y -= h
            pop.geometry(f"{w}x{h}+{r - w - 16}+{max(t, y)}")
            y -= 10

    # ---- process rules
    def _on_rule(self, rule, proc, value):
        what = {"cpu": "CPU", "gpu": "GPU", "memory": "memory"}[rule["metric"]]
        text = f"{proc['name']} (PID {proc['pid']}): {what} {fmt_rule_value(rule, value)} — rule “{rule['key']}”"
        log(f"rule {rule['key']} ({rule['action']}): {text}")
        done = None
        if rule["action"] == "end":
            msg = end_process(proc["pid"])
            done = "ended" if msg.startswith("Ended") else "couldn't end"
            self.status(msg, 10)
        if rule["action"] in ("warn", "end"):
            self.rule_popup.add(rule, proc, value, done)
            if rule["action"] == "warn":
                play_sound(False)
        if SETTINGS.get("tg_rules", True) and rule["action"] in ("warn", "end", "notify"):
            verb = {"warn": "⚠", "end": "⛔ ended", "notify": "ℹ"}[rule["action"]]
            self.telegram.send(f"{verb} {text}", key=f"rule:{rule['key']}:{proc['pid']}")

    # ---- cool-down
    def _on_cooldown(self, change):
        if change == "on":
            self.status(f"❄ Cool-down on: {self.cooldown.describe()} until it has cooled down.", 12)
        else:
            self.status("Cool-down off: the CPU is back to full speed.", 8)
        if self.popup.is_open():
            self.popup.render()

    # ---- history
    def open_history(self):
        if self.hist_win is not None and self.hist_win.winfo_exists():
            self.hist_win.show(self.hist_win.days)
            self.hist_win.deiconify()
            self.hist_win.lift()
            return
        self.hist_win = HistoryWindow(self)

    # ---- alarm
    def _on_alarm_event(self, level, metric, value):
        name = AlarmEngine.NAMES[metric]
        log(f"alarm {level}: {name} = {value}")
        send = SETTINGS.get("tg_send", "critical")
        if level != "ok" and send != "off" and (level == "critical" or send == "warning"):
            v = fmt_temp(value) if metric != "ram" else f"{value:.0f} %"
            self.telegram.send(f"{'🔥' if level == 'critical' else '⚠'} {name} {level}: {v}"
                               + (f" — busiest: {self.top_processes(1)[0]['name']}" if self.procs else ""),
                               key=f"alarm:{metric}:{level}")
        if level == "ok":
            if not self.alarm.level:
                if self.countdown:
                    self.cancel_countdown(reason="back to normal")
                if self.popup.is_open():
                    self.popup.render()
                    self.after(6000, lambda: self.popup.hide() if not self.alarm.level
                               and time.time() >= self.popup.test_until else None)
            elif self.popup.is_open():
                self.popup.render()
            return
        play_sound(level == "critical")
        self._last_sound = time.time()
        if level == "critical" and metric != "ram" and not self.countdown \
                and SETTINGS["action"] in ("shutdown", "sleep", "kill_top"):
            self.start_countdown(SETTINGS["action"])
        self.popup.render()

    def start_countdown(self, action, seconds=None):
        seconds = seconds or SETTINGS.get("countdown", 60)
        target = None
        if action == "kill_top":
            top = [p for p in self.top_processes(5) if not is_protected(p["name"], p["pid"])]
            if not top:
                self.status("No process to end.")
                return
            target = top[0]
        self.countdown = {"action": action, "end": time.time() + seconds,
                          "target": target["name"] if target else None, "pid": target["pid"] if target else None}
        log(f"countdown: {action} in {seconds} s")
        self.popup.render()

    def cancel_countdown(self, reason="cancelled"):
        if self.countdown:
            log(f"countdown {reason}")
            self.countdown = None
            if self.popup.is_open():
                self.popup.render()

    def snooze_alarm(self):
        self.alarm.snooze_until = time.time() + SETTINGS.get("snooze", 10) * 60
        # warnings go quiet; critical ones still show
        self.alarm.level = {m: l for m, l in self.alarm.level.items() if l == "critical"}
        if not self.alarm.level:
            self.popup.hide()
        else:
            self.popup.render()

    def dismiss_alarm(self):
        self.popup.test_until = 0
        self.popup.hide()

    def test_alarm(self):
        """Show what the alarm looks and sounds like (for 15 seconds), and send a test to the phone."""
        self.popup.test_until = time.time() + 15
        play_sound(False)
        self.popup.render()
        if self.telegram.configured():
            self.status("Sending a test message to your phone…", 10)
            self.telegram.send("✅ System Monitor test — phone alerts work.",
                               done=lambda ok, msg: self.after(0, lambda: self.status(f"Phone alert: {msg}", 12)))
        self.after(15200, lambda: self.popup.hide() if not self.alarm.level and self.popup.is_open() else None)

    def _tick(self):
        """Every second: countdown, repeated sound while critical, live values in the pop-up."""
        if self._quitting:
            return
        try:
            cd = self.countdown
            if cd and time.time() >= cd["end"]:
                self.countdown = None
                log(f"countdown finished: {cd['action']}")
                if cd["action"] == "kill_top" and cd.get("pid"):
                    self.end_process(cd["pid"])
                else:
                    system_action(cd["action"])
            if self.popup.is_open():
                if self.alarm.worst() == "critical" and time.time() - self._last_sound > 5:
                    play_sound(True)
                    self._last_sound = time.time()
                self.popup.render()
        except Exception:
            log("tick error:\n" + traceback.format_exc())
        finally:
            if not self._quitting:
                self.after(1000, self._tick)

    # ---- settings
    @staticmethod
    def _mtime():
        try:
            return os.path.getmtime(SETTINGS_FILE)
        except OSError:
            return None

    def _check_settings(self):
        if self._quitting:
            return
        m = self._mtime()
        if m != self.settings_mtime:
            self.settings_mtime = m
            warnings = load_settings()
            self.overlay.apply_settings()
            self.status("Settings reloaded ✓" + (" — " + "; ".join(warnings) if warnings else ""), 8)
        self.after(2000, self._check_settings)

    def show_main(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def set_overlay_corner(self, corner):
        """Move the overlay and remember it in system_monitor.ini."""
        SETTINGS["ov_corner"] = corner
        self.overlay._drag_from = None
        self.overlay.place_corner()
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                text = f.read()
            new, n = re.subn(r"^corner\s*=.*$", f"corner = {corner}", text, count=1, flags=re.M)
            if n:
                with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                    f.write(new)
                self.settings_mtime = self._mtime()
        except OSError:
            pass

    def open_settings(self):
        if IS_WIN:
            os.startfile(SETTINGS_FILE)
        else:
            self.status(SETTINGS_FILE)

    def _on_close(self):
        if SETTINGS.get("close_to_taskbar", True):
            self.iconify()
            self.status("Still watching temperatures from the taskbar. To close it for good: Quit (bottom right) "
                        "or Ctrl+Q.", 10)
            return
        self.quit_app()

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        for step in (self.history.flush, lambda: self.cooldown.release()):
            try:
                step()
            except Exception:
                log("quit step error:\n" + traceback.format_exc())
        self.sensors.stop.set()
        log("quit")
        self.destroy()


def claim_single_instance():
    """True if no other System Monitor runs; otherwise bring that one to the front."""
    if not IS_WIN:
        return True
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    global _MUTEX
    _MUTEX = k.CreateMutexW(None, False, "Local\\Mythos.SystemMonitor")
    if _MUTEX and ctypes.get_last_error() != 183:
        return True
    u = ctypes.windll.user32
    h = u.FindWindowW(None, TITLE)
    if h:
        u.ShowWindow(h, 9)
        u.SetForegroundWindow(h)
    return False


# ======================================================================
# The app's icon (drawn in code: no image files needed)
# ======================================================================

def encode_png(w, h, rgba):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _in_rrect(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _lerp(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def _seg_dist(x, y, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return ((x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2) ** 0.5


def _render(size, shade, ss):
    """size×size pixels: shade(x, y) in 0..1 → (r, g, b, a 0..1) or None; ss×ss samples per pixel."""
    out = bytearray(size * size * 4)
    n = size * ss
    for oy in range(size):
        for ox in range(size):
            r = g = b = a = 0.0
            for sy in range(ss):
                y = (oy * ss + sy + 0.5) / n
                for sx in range(ss):
                    c = shade((ox * ss + sx + 0.5) / n, y)
                    if c:
                        w = c[3]
                        r, g, b, a = r + c[0] * w, g + c[1] * w, b + c[2] * w, a + w
            if a:
                i = (oy * size + ox) * 4
                out[i:i + 4] = bytes((int(r / a), int(g / a), int(b / a), int(255 * a / (ss * ss))))
    return encode_png(size, size, out)


ICON_COLORS = ((0x2e, 0xc4, 0x8f), (0x1f, 0x7a, 0xd6))  # green → blue tile
# a heartbeat line across the tile, like a live chart
_PULSE = [(0.14, 0.58), (0.34, 0.58), (0.43, 0.36), (0.53, 0.76), (0.62, 0.48), (0.68, 0.58), (0.86, 0.58)]


def app_icon_png(size):
    """The app's icon: a rounded green → blue tile with a white pulse line and a soft area under it."""
    c1, c2 = ICON_COLORS
    white = (255, 255, 255)
    width = max(0.055, 1.25 / size)  # the line stays visible at 16 px

    def shade(x, y):
        if not _in_rrect(x, y, 0.04, 0.04, 0.96, 0.96, 0.23):
            return None
        tile = _lerp(c1, c2, x * 0.5 + y * 0.5)
        if y < 0.5:
            tile = _lerp(tile, white, (0.5 - y) * 0.16)
        d = min(_seg_dist(x, y, _PULSE[i], _PULSE[i + 1]) for i in range(len(_PULSE) - 1))
        if d <= width / 2:
            return white + (1.0,)
        if 0.14 <= x <= 0.86 and 0.62 < y < 0.80 and size >= 32:  # baseline bars, like a chart's floor
            col = int((x - 0.14) / 0.72 * 6)
            bx = 0.14 + col * 0.12
            if bx + 0.02 <= x <= bx + 0.10 and y > 0.80 - (0.06 + 0.02 * ((col * 5) % 4)):
                return _lerp(tile, white, 0.45) + (1.0,)
        return tile + (1.0,)
    return _render(size, shade, 3 if size <= 64 else 2 if size <= 128 else 1)


def make_ico(pngs):
    """A Windows .ico from {size: png bytes}."""
    sizes = sorted(pngs)
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for sz in sizes:
        png = pngs[sz]
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return head + entries + data


def set_window_icon(root):
    """The icon in the title bar and the taskbar (every window of the app uses it)."""
    try:
        root._app_icons = [tk.PhotoImage(data=base64.b64encode(app_icon_png(s)).decode()) for s in (64, 32, 16)]
        root.iconphoto(True, *root._app_icons)
    except tk.TclError:
        pass


def move_old_files():
    """Older versions lived next to Window Manager: bring their settings and history into this folder once."""
    parent = os.path.dirname(APP_DIR)
    if not os.path.isfile(os.path.join(parent, "window_manager.py")) and \
            not os.path.isfile(os.path.join(parent, "WindowManager.exe")):
        return
    for name in ("system_monitor.ini", "system_monitor_history.csv", "system_monitor_cooldown.json"):
        old, new = os.path.join(parent, name), os.path.join(APP_DIR, name)
        if os.path.isfile(old) and not os.path.exists(new):
            try:
                shutil.move(old, new)
                log(f"moved {name} from {parent}")
            except OSError:
                pass


_MUTEX = None


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "icon":  # build.bat: python system_monitor.py icon system_monitor.ico
        with open(sys.argv[2], "wb") as fh:
            fh.write(make_ico({s: app_icon_png(s) for s in (16, 24, 32, 48, 64, 128, 256)}))
        print("wrote", sys.argv[2])
        return 0
    if IS_WIN:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass
    move_old_files()
    load_settings()
    if psutil is None:
        msg = "System Monitor needs the psutil package:\n\n    python -m pip install psutil"
        print(msg, file=sys.stderr)
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(TITLE, msg)
        except tk.TclError:
            pass
        return 1
    if not claim_single_instance():
        return 0
    log(f"start {VERSION}; python={sys.version.split()[0]}")
    app = MonitorApp(minimized="--minimized" in sys.argv)
    app.bind_all("<Control-q>", lambda e: app.quit_app())
    try:
        import signal
        signal.signal(signal.SIGINT, lambda *a: app.after(0, app.quit_app))
    except (ValueError, OSError):
        pass

    def keep_alive():  # lets Ctrl+C reach Python while Tk waits
        if not app._quitting:
            app.after(300, keep_alive)
    keep_alive()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

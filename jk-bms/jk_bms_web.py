#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# JK-BMS Dashboard – Version 1.9.5 by M.Vöge (mit Chart-Optimierungen)
# for Venus OS Large 3.7x Raspberry Pi
# D-Bus-Treiber wird benötigt von https://github.com/mr-manuel/venus-os_dbus-serialbattery

import dbus
import sys
import signal
import json
import urllib.request
import os
import time
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from socketserver import ThreadingMixIn
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

# ====================================== WARNSTUFEN – HIER ANPASSEN =======================================
# D-Bus anpassen! Prüfe mit: dbus -y | grep battery
DASHBOARD_NAME = ""                                     # Tilel
MAX_PACK_VOLTAGE_WARNING = 29.40                        # Ab dieser Pack-Spannung: rote Warnung + Blinken
MAX_CELL_VOLTAGE_WARNING = 3.65                         # Ab dieser Zellspannung: Zelle rot + ⚠️
BALANCING_START_DELTA = 0.005                           # Ab dieser Delta gilt Balancing als aktiv
BALANCING_START_VOLTAGE = 3.40                          # Ab dieser Wert wird Balancing als aktiv
LOW_VOLTAGE_WARNING = 24.00                             # Warnung ab dieser Spannung (0.0 = deaktivieren)
LOW_SOC_WARNING = 25                                    # Warnung ab diesem SOC in % (0 = deaktivieren)
MIN_CHARGE_CURRENT_FOR_PULSE = 1.0                      # Ladestrom muss mind. X A sein für Puls (Absorption)
PORT = 99                                               # Webserver-Port (Standard: 99)
TASMOTA_IPS = ["192.168.0.14", "192.168.0.17"]          # Tasmota Geräte – IPs hier eintragen
# =========================================================================================================
battery_services = []
mppt_services = []
primary_battery_service = None

USE_VICTRON_DAILY_YIELD = True
HISTORY_AUTOSAVE_INTERVAL = 300

DEBUG = False # False / True

VERSION = "1.9.5"
SCRIPT_PATH = os.path.abspath(__file__)
SCRIPT_NAME = os.path.basename(__file__)
LOG_FILE = "/var/volatile/tmp/jk_bms_dashboard.log"
HISTORY_FILE = "/var/volatile/tmp/bms_history.json"
HISTORY_BACKUP_FILE = "/data/apps/jk-bms/bms_history_backup.json"

server_start_time = None
history_data = {
    "mppt_pv_power":   [],
    "mppt_pv_voltage": [],
    "consumption":     [],
    "charging":        []
}
bms_data = {}
data_lock = threading.Lock()
last_update = 0

DBUS_CACHE_TTL = 1.0
_dbus_cache = {}
_dbus_cache_lock = threading.Lock()
dbus_available = True

def log_message(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full = f"[{timestamp}] [{level}] {message}"
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(full + "\n")
        if DEBUG or level in ["ERROR", "WARNING"]:
            print(full)
    except:
        pass

def get_running_pids():
    pids = []
    try:
        for pid_str in os.listdir('/proc'):
            if pid_str.isdigit():
                path = f'/proc/{pid_str}/cmdline'
                if os.path.exists(path):
                    with open(path) as f:
                        cmd = f.read().replace('\x00', ' ')
                    if SCRIPT_NAME in cmd and 'run_server' in cmd:
                        pids.append(int(pid_str))
    except:
        pass
    return pids

if len(sys.argv) > 1:
    cmd = sys.argv[1].lower()
    if cmd == "start":
        if get_running_pids():
            print("Dashboard läuft bereits.")
            print(f"Log: tail -f {LOG_FILE}")
            sys.exit(0)
        print("Starte Dashboard...")
        log_message("Dashboard wird gestartet")
        try:
            open(LOG_FILE, 'w').close()
        except:
            pass
        subprocess.Popen(
            f'"{sys.executable}" "{SCRIPT_PATH}" run_server >> "{LOG_FILE}" 2>&1 &',
            shell=True
        )
        time.sleep(1.2)
        print(f"→ http://<IP>:{PORT}")
        print(f"Log: tail -f {LOG_FILE}")
        sys.exit(0)

    elif cmd == "run_server":
        try:
            open(LOG_FILE, 'w').close()
        except:
            pass

    elif cmd == "stop":
        pids = get_running_pids()
        if not pids:
            print("Läuft nicht.")
            sys.exit(0)
        print(f"Stoppe {len(pids)} Instanz(en)...")
        log_message(f"Stoppe Dashboard (PIDs: {pids})")
        for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]:
            for pid in pids[:]:
                try:
                    os.kill(pid, sig)
                except:
                    pass
            time.sleep(3.0 if sig == signal.SIGKILL else 2.0)
        print("Beendet.")
        sys.exit(0)

    elif cmd == "restart":
        os.system(f'"{sys.executable}" "{SCRIPT_PATH}" stop')
        time.sleep(1.2)
        os.system(f'"{sys.executable}" "{SCRIPT_PATH}" start')
        sys.exit(0)

    elif cmd == "status":
        pids = get_running_pids()
        if pids:
            print(f"Läuft (PIDs: {', '.join(map(str,pids))}) → http://<IP>:{PORT}")
        else:
            print("Läuft nicht.")
        sys.exit(0)

    else:
        print("Befehle: start | stop | restart | status")
        sys.exit(1)

if len(sys.argv) == 1:
    os.execv(sys.executable, [sys.executable, SCRIPT_PATH, "start"])

# D-Bus Setup
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
from vedbus import VeDbusItemImport

DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()

_dbus_item_cache = {}

def get_dbus_value(path, default=0, service=None):
    global dbus_available, primary_battery_service

    current_service = service if service else primary_battery_service
    
    if not current_service:
        return default

    key = (current_service, path)
    now = time.monotonic()

    with _dbus_cache_lock:
        if key in _dbus_cache:
            ts, val = _dbus_cache[key]
            if now - ts < DBUS_CACHE_TTL:
                return val

    try:
        if key not in _dbus_item_cache:
            log_message(f"Initialisiere D-Bus Pfad: {path} auf {current_service}")
            _dbus_item_cache[key] = VeDbusItemImport(bus, current_service, path)
        
        item = _dbus_item_cache[key]
        val = item.get_value()
        
        if val is None:
            val = default

        with _dbus_cache_lock:
            _dbus_cache[key] = (now, val)

        dbus_available = True
        return val

    except Exception as e:
        if key in _dbus_item_cache:
            del _dbus_item_cache[key]
        dbus_available = False
        return default

def discover_dbus_services():
    global mppt_services, battery_services, primary_battery_service, bms_data, _dbus_item_cache
    try:
        with _dbus_cache_lock:
            _dbus_item_cache.clear()
            _dbus_cache.clear()
            
        names = bus.list_names()

        mppt_services = [n for n in names if str(n).startswith("com.victronenergy.solarcharger.")]
        if mppt_services:
            log_message(f"MPPT(s) gefunden: {', '.join(mppt_services)}")
            mppt = mppt_services[0]
            val = get_dbus_value_immediate(mppt, '/History/Daily/0/Yield')
            if val is not None:
                with data_lock:
                    bms_data['daily_pv_yield'] = round(float(val), 3)
                log_message(f"Initialer Tagesertrag von {mppt} geladen: {val} kWh")
            
            val_yesterday = get_dbus_value_immediate(mppt, '/History/Daily/1/Yield')
            if val_yesterday is not None:
                with data_lock:
                    bms_data['yield_yesterday'] = round(float(val_yesterday), 3)
        else:
            log_message("Kein MPPT (solarcharger) gefunden", "WARNING")

        battery_services = [n for n in names if str(n).startswith("com.victronenergy.battery.")]
        if battery_services:
            primary_battery_service = battery_services[0]
            log_message(f"Batterie(n) gefunden: {', '.join(battery_services)}")
            log_message(f"Nutze '{primary_battery_service}' als Haupt-BMS")
        else:
            log_message("Keine Batterie (com.victronenergy.battery.*) gefunden!", "ERROR")

    except Exception as e:
        log_message(f"Fehler bei der Dienst-Erkennung: {e}", "ERROR")

def get_mppt_daily_yield(day_offset=0):
    if not mppt_services:
        return None
    total = 0.0
    for svc in mppt_services:
        val = get_dbus_value(f'/History/Daily/{day_offset}/Yield', None, svc)
        if isinstance(val, (int, float)):
            total += float(val)
    return total if total > 0 else None

def dbus_poller():
    log_message("D-Bus Poller gestartet")
    consecutive_errors = 0
    
    while True:
        if not primary_battery_service or consecutive_errors > 5:
            if consecutive_errors > 5:
                log_message("Zu viele Fehler im Poller. Starte Neu-Erkennung der Dienste...", "WARNING")
            else:
                log_message("Warte auf Batterie-Dienst...", "WARNING")
            
            discover_dbus_services()
            consecutive_errors = 0
            time.sleep(5)
            continue
        
        try:
            temp = {
                'soc':         get_dbus_value('/Soc'),
                'voltage':     get_dbus_value('/Dc/0/Voltage'),
                'current':     get_dbus_value('/Dc/0/Current'),
                'power':       get_dbus_value('/Dc/0/Power'),
                'temperature': get_dbus_value('/Dc/0/Temperature'),
                'min_cell':    get_dbus_value('/System/MinCellVoltage'),
                'max_cell':    get_dbus_value('/System/MaxCellVoltage'),
                'cells':       [],
                'pv_voltage':  0.0,
                'pv_power':    0.0,
            }

            for i in range(1, 9):
                v = get_dbus_value(f'/Voltages/Cell{i}')
                if v is not None and v > 0.1:
                    temp['cells'].append(round(v, 3))
                else:
                    break
            
            if not temp['cells']:
                temp['cells'] = [0.000] * 8

            for svc in mppt_services:
                v = get_dbus_value('/Pv/V', 0, svc) or get_dbus_value('/Pv/0/V', 0, svc)
                p = get_dbus_value('/Yield/Power', 0, svc) or get_dbus_value('/Pv/0/P', 0, svc)
                temp['pv_voltage'] += v
                temp['pv_power'] += p

            with data_lock:
                bms_data.update(temp)
                global last_update
                bms_data["dbus_ok"] = dbus_available
                last_update = int(time.time() * 1000)
            
            consecutive_errors = 0

        except Exception as e:
            consecutive_errors += 1
            log_message(f"Poller Fehler ({consecutive_errors}/5): {e}", "ERROR")
            time.sleep(1)

        time.sleep(1.0)

def load_history():
    global history_data
    source = HISTORY_FILE

    if not os.path.exists(source):
        if os.path.exists(HISTORY_BACKUP_FILE):
            mtime = os.path.getmtime(HISTORY_BACKUP_FILE)
            age = time.time() - mtime
            if age < 600:
                source = HISTORY_BACKUP_FILE
                log_message(f"Lade Backup aus /data (Alter: {int(age)}s)")
            else:
                log_message(f"Backup in /data zu alt ({int(age)}s), wird ignoriert.")
                return
        else:
            return

    try:
        with open(source, "r") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict) and "mppt_pv_power" in loaded:
                with data_lock:
                    history_data = loaded
                log_message(f"History erfolgreich aus {source} geladen.")
    except Exception as e:
        log_message(f"History Laden Fehler: {e}", "ERROR")

def save_history(backup=False):
    """
    Speichert atomar ins RAM. Wenn backup=True, auch permanent nach /data.
    """
    try:
        with data_lock:
            if not any(history_data.values()): return
            data_str = json.dumps(history_data, separators=(',', ':'), ensure_ascii=False)

        targets = [HISTORY_FILE]
        if backup:
            targets.append(HISTORY_BACKUP_FILE)

        for path in targets:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                f.write(data_str)
            os.replace(tmp, path)
        
        if DEBUG:
            log_message(f"History gesichert (Permanent-Backup: {backup})")
    except Exception as e:
        log_message(f"Fehler beim Speichern: {e}", "ERROR")

def cleanup_old_data():
    if datetime.now().year < 2024:
        log_message("Systemzeit ungenau (NTP fehlt) – Cleanup übersprungen.")
        return

    now = datetime.now()
    cutoff_dt = now.replace(hour=4, minute=0, second=0, microsecond=0)

    if now < cutoff_dt:
        cutoff_dt = cutoff_dt - timedelta(days=1)
        
    cutoff_ms = int(cutoff_dt.timestamp() * 1000)
    
    with data_lock:
        for k in history_data:
            history_data[k] = [p for p in history_data[k] if p.get("x", 0) > cutoff_ms]
    
    if DEBUG:
        log_message(f"History-Cleanup: Daten vor {cutoff_dt.strftime('%H:%M')} gelöscht.")

def get_dbus_value_immediate(service, path):
    try:
        obj = bus.get_object(service, path)
        interface = dbus.Interface(obj, 'com.victronenergy.BusItem')
        return interface.GetValue()
    except:
        return None

def history_autosaver():
    while True:
        time.sleep(HISTORY_AUTOSAVE_INTERVAL)
        save_history()

def collect_data():
    log_message("History-Sammler gestartet (5s Intervall mit Mittelwertbildung)")
    samples = []
    last_save_time = time.time()

    while True:
        try:
            if datetime.now().year < 2024:
                time.sleep(30)
                continue

            with data_lock:
                if time.time() - (last_update / 1000) > 10:
                    time.sleep(5)
                    continue
                    
                v = bms_data.get('voltage', 0)
                p_pv = bms_data.get('pv_power', 0)
                v_pv = bms_data.get('pv_voltage', 0)
                p_batt = bms_data.get('power', 0)

            if v <= 0.1:
                time.sleep(10)
                continue

            samples.append({
                'p_pv': p_pv,
                'v_pv': v_pv,
                'p_batt': p_batt
            })

            now = time.time()
            if now - last_save_time >= 60:
                if samples:
                    avg_p_pv = sum(s['p_pv'] for s in samples) / len(samples)
                    avg_v_pv = sum(s['v_pv'] for s in samples) / len(samples)
                    avg_p_batt = sum(s['p_batt'] for s in samples) / len(samples)
                    
                    now_ms = int(now * 1000)
                    
                    with data_lock:
                        history_data["mppt_pv_power"].append({"x": now_ms, "y": round(avg_p_pv)})
                        history_data["mppt_pv_voltage"].append({"x": now_ms, "y": round(avg_v_pv, 1)})
                        history_data["consumption"].append({"x": now_ms, "y": round(abs(avg_p_batt)) if avg_p_batt < 0 else 0})
                        history_data["charging"].append({"x": now_ms, "y": round(avg_p_batt) if avg_p_batt > 0 else 0})

                if USE_VICTRON_DAILY_YIELD and mppt_services:
                    vy_today = get_mppt_daily_yield(0)
                    vy_yesterday = get_mppt_daily_yield(1)
                   
                    now_hour = datetime.now().hour
                    if (
                        vy_today is not None
                       and vy_yesterday is not None
                       and now_hour < 4
                       and abs(vy_today - vy_yesterday) < 0.001
                    ):
                       vy_today = 0.0

                    with data_lock:
                        if vy_today is not None:
                            bms_data['daily_pv_yield'] = round(vy_today, 3)
                        if vy_yesterday is not None:
                            bms_data['yield_yesterday'] = round(vy_yesterday, 3)

                cleanup_old_data()
                samples = []
                last_save_time = now

            time.sleep(5) 

        except Exception as e:
            log_message(f"collect_data Fehler: {e}", "ERROR")
            samples = []
            time.sleep(10)

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    request_queue_size = 8

class BMSHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def proxy_tasmota(self):
        try:
            path_parts = self.path.split('/')
            if len(path_parts) < 3:
                raise ValueError("Keine IP angegeben")
            ip = path_parts[2].split('?')[0]
            query = self.path.split('cmd=')[1] if 'cmd=' in self.path else 'Power'
            if ip not in TASMOTA_IPS:
                raise ValueError(f"Unbekannte Tasmota-IP: {ip}")
            
            url = f"http://{ip}/cm?cmnd={query}"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'VenusOS-Dashboard-Proxy')
            
            with urllib.request.urlopen(req, timeout=5) as response:
                res_data = response.read()
                
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(res_data)
        except Exception as e:
            log_message(f"Tasmota Proxy Fehler {self.path}: {e}", "ERROR")
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            fallback = {"POWER": "OFF"}
            if "FriendlyName1" in self.path:
                fallback["FriendlyName1"] = "Offline"
            self.wfile.write(json.dumps(fallback).encode())

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self.serve_html()
        elif self.path == '/data':
            self.serve_json_data()
        elif self.path == '/history':
            self.serve_history()
        elif self.path.startswith('/tasmota/'):
            self.proxy_tasmota()
        elif self.path.startswith('/static/'):
            self.serve_static()
        else:
            self.send_error(404)

    def serve_history(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        with data_lock:
            history_json = json.dumps(history_data, separators=(',', ':'))
        self.wfile.write(history_json.encode())

    def serve_static(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base_dir, self.path.lstrip('/'))
        
        if not os.path.isfile(path):
            self.send_error(404)
            return
        self.send_response(200)
        if path.endswith(".js"):
            self.send_header("Content-Type", "application/javascript")
        self.end_headers()
        with open(path, "rb") as f:
            self.wfile.write(f.read())

    def serve_json_data(self):
        with data_lock:
            d = bms_data.copy()

        soc = d.get('soc', 0)
        voltage = d.get('voltage', 0)
        current = d.get('current', 0)
        power = d.get('power', 0)
        minc = d.get('min_cell', 0)
        maxc = d.get('max_cell', 0)
        delta = round(maxc - minc, 3) if minc and maxc else 0

        temp = d.get('temperature')
        if temp is not None and -20 <= temp <= 80:
            temp = round(temp, 1)
        else:
            temp = None

        bal_active = (delta > BALANCING_START_DELTA) and (maxc >= BALANCING_START_VOLTAGE)
        bal_text = "⚡" if bal_active else "Inaktiv"
        bal_color = "#ff9500" if bal_active else "#888"

        cells = d.get('cells', [0.000] * 8)

        data = {
            "soc": soc,
            "voltage": voltage,
            "dbus_ok": d.get("dbus_ok", False),
            "current": current,
            "power": power,
            "battery_power": power,
            "balancing": {"text": bal_text, "color": bal_color, "active": bal_active},
            "delta": delta,
            "delta_color": "#0f0" if delta < 0.020 else "#ff0" if delta < 0.050 else "#f00",
            "min_cell": minc,
            "max_cell": maxc,
            "cells": cells,
            "temperature": temp,
            "pv_voltage": round(d.get('pv_voltage', 0), 1),
            "pv_power": round(d.get('pv_power', 0)),
            "daily_pv_yield": round(d.get('daily_pv_yield', 0), 3),
            "yield_yesterday": round(d.get('yield_yesterday', 0), 3)
        }

        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(data, separators=(',', ':')).encode())

    def serve_html(self):
        soc_init = get_dbus_value('/Soc', 0)
        volt_init = get_dbus_value('/Dc/0/Voltage', 0)
        start_ts = int(server_start_time.timestamp() * 1000) if server_start_time else int(time.time() * 1000)
        tasmota_ips_json = json.dumps(TASMOTA_IPS, ensure_ascii=False)

        html = f"""<!DOCTYPE html>
<html lang="de" data-theme="dark">
<head>
<meta charset="utf-8">
<title>{DASHBOARD_NAME}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script src="/static/chart.js"></script>
<script src="/static/luxon.js"></script>
<script src="/static/chartjs-adapter-luxon.js"></script>
<style>
    :root {{--bg:#1e1e1e; --card:#2d2d2d; --text:#fff; --accent:#00bfff; --shadow:rgba(0,0,0,0.3); --gray:#aaa; --darkgray:#555;}}
    [data-theme="light"] {{--bg:#f5f5f5; --card:#fff; --text:#333; --accent:#007acc; --shadow:rgba(0,0,0,0.1); --gray:#666; --darkgray:#888;}}
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;padding:15px;transition:background .3s,color .3s}}
    .container{{max-width:1400px;margin:0 auto;width:100%}}
    :fullscreen .container, :-webkit-full-screen .container {{max-width: 98vw; padding: 10px;}}
    h1{{text-align:center;color:var(--accent);font-size:2.2rem;margin-bottom:15px}}
    
    /* Toast & Alerts */
    .status-toast{{position:fixed;top:20px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,.85);color:#fff;padding:12px 24px;border-radius:12px;font-size:1.1rem;font-weight:bold;z-index:10000;box-shadow:0 4px 15px rgba(0,0,0,.5);opacity:0;transition:opacity .6s ease-in-out;pointer-events:none;max-width:90%;text-align:center;border:2px solid}}
    .status-toast.show{{opacity:1}}
    .status-toast.warning{{border-color:#ff8800;background:rgba(255,136,0,.25)}}
    .status-toast.error{{border-color:#ff0000;background:rgba(255,0,0,.25)}}
    .status-balance{{position:fixed;top:20px;left:50%;transform:translateX(-50%);background:var(--card);color:var(--text);padding:15px 25px;border-radius:16px;font-size:1.2rem;font-weight:bold;box-shadow:0 4px 15px var(--shadow);z-index:9998;opacity:0;transition:opacity .6s ease-in-out;pointer-events:none;text-align:center;max-width:90%}}
    .status-balance.show{{opacity:1}}
    .status-balance i{{color:#00bfff;font-size:1.3rem;margin:0 8px}}
    
    .high-voltage-warning,.low-voltage-warning,.low-soc-warning{{display:none;text-align:center;font-weight:bold}}
    .high-voltage-warning{{color:#f00;animation:blink 1s infinite}}
    .low-voltage-warning{{color:#ff8800}}
    .low-soc-warning{{color:#ff6600;animation:softBlink 2s infinite}}
    
    @keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
    @keyframes softBlink{{0%,100%{{opacity:1}}50%{{opacity:.5}}}}
    /* Interaktive Controls (Toggle & Temp) */
    .theme-toggle, .temp-circle {{position: fixed;top: 15px;background: var(--card);border: none;width: 50px;height: 50px;border-radius: 50%;cursor: pointer;box-shadow: 0 4px 10px var(--shadow);display: flex;align-items: center;justify-content: center;z-index: 200;transition: opacity 0.5s ease, visibility 0.5s;opacity: 0;visibility: hidden;}}
    .theme-toggle {{right: 15px; font-size: 1.6rem;}}
    .temp-circle {{left: 15px; font-size: 1.2rem; color: var(--gray);}}
    .controls-visible {{opacity: 1 !important;visibility: visible !important;}}
    .theme-toggle:hover, .temp-circle:hover {{transform: scale(1.1); background: rgba(0,191,255,0.12);}}

    /* Tasmota Kreise – rechts gestapelt */
    .tasmota-circle {{position: fixed; right: 15px; background: var(--card); border: none;width: 50px; height: 50px; border-radius: 50%;cursor: pointer; box-shadow: 0 4px 10px var(--shadow);display: flex; align-items: center; justify-content: center;z-index: 210; transition: all 0.22s;opacity: 0; visibility: hidden;color: var(--gray); font-size: 0.75rem; font-weight: bold;text-align: center; line-height: 1.1;}}
    .tasmota-on {{ color: #00ff00 !important; }}
    .tasmota-circle:hover {{ transform: scale(1.12); background: rgba(0,191,255,0.15); }}

    /* Layout-Grid */
    .main-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;margin:10px 0}}
    .box{{background:var(--card);padding:25px 15px;border-radius:16px;text-align:center;box-shadow:0 4px 15px var(--shadow)}}
    .big{{font-size:4.8rem;font-weight:700;color:var(--text)}}
    .big span{{font-size:.4em}}
    .label{{font-size:1.1rem;color:var(--gray);margin-top:12px;display:block}}
    .info-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin:10px 0}}
    .info{{background:var(--card);padding:18px 12px;border-radius:12px;text-align:center}}
    .info-label{{font-size:1rem;color:var(--gray);display:block;margin-bottom:8px}}
    .info strong{{font-size:1.6rem;font-weight:bold;display:block}}
    .cells-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:14px;margin:10px 0}}
    .cell{{background:var(--card);padding:16px 8px;border-radius:12px;text-align:center}}
    .cell-num{{font-size:1rem;color:var(--gray);display:block;margin-bottom:8px}}
    .cell-v{{font-size:1.3rem;font-weight:bold;display:block}}
    .high-cell{{color:#f00 !important}}
    .charts-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px;margin:10px 0}}
    .charts-grid .full-width{{grid-column:1/-1}}
    .chart-container{{background:var(--card);border-radius:16px;box-shadow:0 4px 15px var(--shadow)}}
    .chart-content{{padding:10px}}
    canvas{{height:280px !important;width:100% !important}}
    @media (max-width:1199px){{.charts-grid{{grid-template-columns:repeat(3,1fr)}}}}
    @media (max-width:900px){{.charts-grid{{grid-template-columns:1fr}}canvas{{height:250px !important}}}}
    .footer{{text-align:center;margin:1px 0 20px;color:var(--darkgray);font-size:.9rem}}
</style>
</head>
<body>
<div class="container">
    <div class="temp-circle" id="temp-circle"><div id="temperature">—</div></div>
    <div id="tasmota-container"></div>
    <button class="theme-toggle" id="theme-toggle" title="Theme wechseln">🌙</button>
    <h1>{DASHBOARD_NAME}</h1>

    <div class="status-balance" id="balancing-info"><i>⚡</i> Balancing aktiv – Zellen werden ausgeglichen <i>⚡</i></div>
    <div class="status-toast" id="status-toast"><span id="status-text"></span></div>
    <div class="high-voltage-warning" id="high-voltage-warning">⚠️ Max. Ladung erreicht! ({MAX_PACK_VOLTAGE_WARNING} V) ⚠️</div>
    <div class="low-voltage-warning" id="low-voltage-warning">⚠️ Niedrige Spannung! ({LOW_VOLTAGE_WARNING} V) ⚠️</div>
    <div class="low-soc-warning" id="low-soc-warning">⚠️ Batterie fast leer! (≤ {LOW_SOC_WARNING}%) ⚠️</div>

    <div class="main-grid">
        <div class="box"><div class="big" id="voltage">{volt_init:.2f}</div><div class="label">Batterie Spannung (Volt)</div></div>
        <div class="box"><div class="big" id="soc">{soc_init:.0f}<span>%</span></div><div class="label">Ladezustand (SoC)</div></div>
        <div class="box"><div class="big" id="current">0.00</div><div class="label">BMS Strom (A) (in/out)</div></div>
    </div>

    <div class="info-grid">
        <div class="info"><span class="info-label">PV (MPPT V)</span><strong id="pv_voltage">0.0 V</strong></div>
        <div class="info"><span class="info-label">PV (MPPT Watt)</span><strong id="pv_power">0 W</strong></div>
        <div class="info"><span class="info-label">Lade/Entladung</span><strong id="power">0 W</strong></div>
        <div class="info" onclick="toggleYieldDisplay()" style="cursor:pointer"><span class="info-label" id="yield-label">Tagesertrag</span><strong id="daily_pv_yield">0 kWh</strong></div>
        <div class="info"><span class="info-label">Balancing</span><strong id="balancing">—</strong></div>
        <div class="info"><span class="info-label">Zellen-Diff</span><strong id="delta">— V</strong></div>
    </div>

    <div class="cells-grid" id="cells-grid"></div>

    <div class="charts-grid">
        <div class="chart-container full-width" id="mppt-container">
            <div class="chart-content"><canvas id="mpptChart"></canvas></div>
        </div>
    </div>

    <div class="footer">JK-BMS Dashboard {VERSION} – Läuft seit: <span id="uptime">00:00:00</span></div>
</div>

<script>
let mpptChart = null;
let lastBalancingState = false;
let showYesterday = false;
let currentYieldToday = 0;
let currentYieldYesterday = 0;
let retryDelay = 1500;
const MAX_RETRY_DELAY = 10000;

// === Hover Logik für Buttons ===
let idleTimer;
const themeBtn = document.getElementById('theme-toggle');
const tempBtn = document.getElementById('temp-circle');

function showControls() {{
    themeBtn.classList.add('controls-visible');
    // Temperatur nur zeigen, wenn Daten vorhanden sind
    if (document.getElementById('temperature').textContent !== '—') {{
        tempBtn.classList.add('controls-visible');
    }}
    document.querySelectorAll('.tasmota-circle').forEach(el => el.classList.add('controls-visible'));  // ← neu
    
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {{
        themeBtn.classList.remove('controls-visible');
        tempBtn.classList.remove('controls-visible');
        document.querySelectorAll('.tasmota-circle').forEach(el => el.classList.remove('controls-visible'));  // ← neu
    }}, 3500);  // ← Timeout auf 3500 erhöht für mehr Platz
}}

window.addEventListener('mousemove', showControls);
window.addEventListener('touchstart', showControls);

// Restliche Dashboard Logik...
function toggleYieldDisplay() {{
    showYesterday = !showYesterday;
    document.getElementById('yield-label').textContent = 
        showYesterday ? "Tagesertrag (Gestern)" : "Tagesertrag";
    updateYieldDisplay();
}}

function updateYieldDisplay() {{
    const val = showYesterday ? currentYieldYesterday : currentYieldToday;
    const el = document.getElementById('daily_pv_yield');
    
    if (val >= 10)      el.textContent = val.toFixed(1) + ' kWh';
    else if (val >= 1)  el.textContent = val.toFixed(2) + ' kWh';
    else                el.textContent = Math.round(val * 1000) + ' Wh';
    
    el.style.color = val > 0 
        ? (showYesterday ? '#ff9500' : '#00ff00') 
        : 'var(--gray)';
}}

let theme = localStorage.getItem('theme') || 'dark';
document.documentElement.setAttribute('data-theme', theme);
themeBtn.textContent = theme === 'light' ? '☀️' : '🌙';

themeBtn.onclick = () => {{
    theme = theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
    themeBtn.textContent = theme === 'light' ? '☀️' : '🌙';
}}

tempBtn.onclick = () => {{
    const doc = document.documentElement;
    if (!document.fullscreenElement && !document.webkitFullscreenElement) {{
        if (doc.requestFullscreen) doc.requestFullscreen();
        else if (doc.webkitRequestFullscreen) doc.webkitRequestFullscreen();
    }} else {{
        if (document.exitFullscreen) document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
    }}
}};

const startTime = {start_ts};
function updateUptime() {{
    let s = Math.floor((Date.now() - startTime) / 1000);
    let h = Math.floor(s / 3600).toString().padStart(2, '0');
    let m = Math.floor((s % 3600) / 60).toString().padStart(2, '0');
    let sec = (s % 60).toString().padStart(2, '0');
    document.getElementById('uptime').textContent = `${{h}}:${{m}}:${{sec}}`;
}}
setInterval(updateUptime, 1000);
updateUptime();

function initMpptChart() {{
    const ctx = document.getElementById('mpptChart').getContext('2d');
    mpptChart = new Chart(ctx, {{
        type: 'line',
        data: {{ datasets: [
            {{ label: 'PV (W)',     borderColor: '#00ff00', backgroundColor: 'rgba(0,255,0,0.05)', data: [], tension: 0.15, fill: true,  pointRadius: 0.1, borderWidth: 1, yAxisID: 'y1' }},
            {{ label: 'PV (V)',     borderColor: '#0088ff', backgroundColor: 'rgba(0,136,255,0.05)', data: [], tension: 0.15, fill: true,  pointRadius: 0.1, borderWidth: 1, yAxisID: 'y' }},
            {{ label: 'Out (W)',    borderColor: '#ff4444', backgroundColor: 'rgba(255,68,68,0.05)', data: [], tension: 0.18, fill: true,  pointRadius: 0.1, borderWidth: 1, yAxisID: 'y1' }},
            {{ label: 'In (W)',     borderColor: '#ff9500', backgroundColor: 'rgba(255,149,0,0.05)', data: [], tension: 0.18, fill: true,  pointRadius: 0.1, borderWidth: 1, yAxisID: 'y1' }}
        ] }},
        options: {{
            animation: {{
                duration: 400,
                easing: 'easeOutQuart'
            }},
            maintainAspectRatio: false,
            interaction: {{
                mode: 'index',
                intersect: false
            }},
            plugins: {{
                legend: {{ position: 'top' }},
                tooltip: {{
                    enabled: true,
                    position: 'nearest',
                    external: function(context) {{
                        const tooltipModel = context.tooltip;
                        if (tooltipModel.opacity !== 0) {{
                            if (window.mpptTimer) clearTimeout(window.mpptTimer);
                            window.mpptTimer = setTimeout(() => {{
                                tooltipModel.opacity = 0;
                                context.chart.setActiveElements([]);
                                context.chart.update();
                            }}, 10000); // 10000ms = 10 Sekunden
                        }}
                    }},
                    callbacks: {{
                        label: function(context) {{
                            let label = context.dataset.label || '';
                            let val = context.parsed.y;
                            let unit = context.dataset.yAxisID === 'y' ? ' V' : ' W';
                            
                            if (unit === ' W') {{
                                if (Math.abs(val) >= 1000) return label + ': ' + (val / 1000).toFixed(2) + ' kW';
                                return label + ': ' + Math.round(val) + ' W';
                            }}
                            return label + ': ' + val.toFixed(2) + unit;
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{ 
                    type: 'time', 
                    time: {{ unit: 'hour', displayFormats: {{ hour: 'HH:mm' }} }}, 
                    grid: {{ color: 'rgba(255, 255, 255, 0.00)' }} 
                }},
                y: {{ 
                    position: 'left', 
                    min: 0, 
                    title: {{ display: true, text: 'Volt' }}, 
                    grid: {{ color: 'rgba(255, 255, 255, 0.03)' }} 
                }},
                y1: {{ 
                    position: 'right', 
                    min: 0, 
                    title: {{ display: true, text: 'Watt' }}, 
                    grid: {{ drawOnChartArea: false }} 
                }}
            }}
        }}
    }});
}}

function cleanupChartData() {{
    const now = new Date();
    let cutoffDate = new Date();
    cutoffDate.setHours(4, 0, 0, 0);
    if (now < cutoffDate) {{
        cutoffDate.setDate(cutoffDate.getDate() - 1);
    }}
    const cutoff = cutoffDate.getTime();
    mpptChart.data.datasets.forEach(ds => {{
        while (ds.data.length > 0 && ds.data[0].x < cutoff) {{
            ds.data.shift();
        }}
    }});
}}

function loadHistory() {{
    fetch('/history').then(r => r.json()).then(h => {{
        if (h.mppt_pv_power)   mpptChart.data.datasets[0].data = h.mppt_pv_power;
        if (h.mppt_pv_voltage) mpptChart.data.datasets[1].data = h.mppt_pv_voltage;
        if (h.consumption)     mpptChart.data.datasets[2].data = h.consumption;
        if (h.charging)        mpptChart.data.datasets[3].data = h.charging;
        cleanupChartData();
        mpptChart.update('quiet');
    }}).catch(() => {{ console.warn("History-Laden fehlgeschlagen"); }});
}}

initMpptChart();
loadHistory();
setInterval(loadHistory, 30000);

function updateData() {{
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);

    fetch('/data', {{ signal: controller.signal }})
    .then(r => {{
        clearTimeout(timeoutId);
        if (!r.ok) throw new Error();
        return r.json();
    }})
    .then(data => {{
        retryDelay = 1500;
        const toast = document.getElementById('status-toast');
        const txt   = document.getElementById('status-text');
        const bal   = document.getElementById('balancing-info');
        const hv_warn = document.getElementById('high-voltage-warning');
        const lv_warn = document.getElementById('low-voltage-warning');
        const ls_warn = document.getElementById('low-soc-warning');

        const conn = data.voltage > 0.1 && data.soc > 0 && data.power !== undefined;
        const bAct = data.balancing?.active || false;

        if (!conn) {{
            txt.textContent = 'Keine gültigen BMS-Daten ⚠️';
            toast.className = 'status-toast show warning';
            hv_warn.style.display = 'none';
            lv_warn.style.display = 'none';
            ls_warn.style.display = 'none';
            setTimeout(updateData, 2000);
            return; 
        }} else {{
            toast.className = 'status-toast';
        }}

        if (bAct && !lastBalancingState) {{
            bal.style.display = 'block';
            bal.style.opacity = '1';
            setTimeout(() => {{
                bal.style.opacity = '0';
                setTimeout(() => {{ bal.style.display = 'none' }}, 600);
            }}, 3000);
        }}
        lastBalancingState = bAct;

        const v = data.voltage || 0;
        const vEl = document.getElementById('voltage');
        vEl.textContent = v.toFixed(2);

        let col = '#f00';
        if (v >= {MAX_PACK_VOLTAGE_WARNING}) col = '#f00';
        else if (v >= 28.0) col = '#ff9500';
        else if (v >= 26.0) col = '#0f0';
        else if (v >= 24.01) col = '#ff0';
        vEl.style.color = col;
        vEl.style.animation = v >= {MAX_PACK_VOLTAGE_WARNING} ? 'blink 1s infinite' : 'none';

        const s = data.soc || 0;
        const socEl = document.getElementById('soc');
        socEl.innerHTML = s.toFixed(0) + '<span>%</span>';
        socEl.style.color = s >= 95 ? '#0f0' : s >= 70 ? '#ff0' : s >= 40 ? '#ff9500' : '#f00';
        socEl.style.animation = s <= 25 ? 'blink 1s infinite' : (data.current >= {MIN_CHARGE_CURRENT_FOR_PULSE}) ? 'blink 3s infinite ease-in-out' : 'none';

        if (v > 1.0) {{
            hv_warn.style.display = v >= {MAX_PACK_VOLTAGE_WARNING} ? 'block' : 'none';
            lv_warn.style.display = ({LOW_VOLTAGE_WARNING} > 0 && v <= {LOW_VOLTAGE_WARNING}) ? 'block' : 'none';
        }}

        if (s > 0) {{
            if ({LOW_SOC_WARNING} > 0 && s <= {LOW_SOC_WARNING}) {{
                ls_warn.innerHTML = `⚠️ Batterie fast leer! (${{s.toFixed(0)}}% ≤ {LOW_SOC_WARNING}%) ⚠️`;
                ls_warn.style.display = 'block';
            }} else {{
                ls_warn.style.display = 'none';
            }}
        }}

        const pwr = data.power || 0;
        const cur = document.getElementById('current');
        const absC = Math.abs(data.current || 0);
        if (absC < 0.1) {{
            cur.textContent = '0.00';
            cur.style.color = 'var(--gray)';
            cur.style.animation = 'none';
        }} else if (data.current > 0) {{
            cur.textContent = '+' + data.current.toFixed(2);
            cur.style.color = '#0f0';
            cur.style.animation = 'none';
        }} else {{
            cur.textContent = data.current.toFixed(2);
            cur.style.color = '#f00';
            cur.style.animation = 'blink 3s infinite ease-in-out';
        }}

        document.getElementById('power').textContent = Math.abs(pwr) > 10 ? (pwr > 0 ? '+' : '') + Math.round(pwr) + ' W' : '0 W';
        document.getElementById('power').style.color = Math.abs(pwr) <= 10 ? 'var(--gray)' : (pwr > 0 ? '#0f0' : '#f00');

        const pvP = Math.round(data.pv_power || 0);
        document.getElementById('pv_power').textContent = pvP + ' W';
        document.getElementById('pv_power').style.color = pvP > 0 ? '#00ff00' : 'var(--gray)';

        const pvV = data.pv_voltage.toFixed(1);
        document.getElementById('pv_voltage').textContent = pvV + ' V';
        document.getElementById('pv_voltage').style.color = parseFloat(pvV) > 0.01 ? '#0088ff' : 'var(--gray)';

        document.getElementById('balancing').textContent = data.balancing.text;
        document.getElementById('balancing').style.color = data.balancing.color;
        document.getElementById('delta').textContent = data.delta.toFixed(3) + ' V';
        document.getElementById('delta').style.color = data.delta_color;

        currentYieldToday = data.daily_pv_yield || 0;
        currentYieldYesterday = data.yield_yesterday || 0;
        updateYieldDisplay();

        if (data.temperature !== null && data.temperature > -40) {{
            document.getElementById('temperature').textContent = Math.round(data.temperature) + '°';
        }}

        const grid = document.getElementById('cells-grid');
        if (grid.children.length === 0) {{
            data.cells.forEach((_, i) => {{
                grid.innerHTML += `
                    <div class="cell" id="cell-container-${{i}}">
                        <div class="cell-num">Zelle ${{String(i+1).padStart(2,'0')}}</div>
                        <div class="cell-v" id="cell-v-${{i}}">0.000 V</div>
                        <div id="cell-warn-${{i}}"></div>
                    </div>`;
            }});
        }}

        data.cells.forEach((v, i) => {{
            const vCellEl = document.getElementById(`cell-v-${{i}}`);
            const warnCellEl = document.getElementById(`cell-warn-${{i}}`);
            let color = (v >= 3.0 && v <= 3.65) ? '#0f0' : (v > 0.1 ? '#ff0' : '#555');
            let cls = '';
            let ico = '';
            if (data.balancing.active) {{
                if (Math.abs(v - data.min_cell) <= 0.0005) color = '#00bfff';
                if (Math.abs(v - data.max_cell) <= 0.0005) color = '#ff9500';
            }}
            if (v >= {MAX_CELL_VOLTAGE_WARNING}) {{
                color = '#ff0000';
                cls = 'high-cell';
                ico = `<div style="font-size:1.2rem;color:orange;margin-top:4px;">⚠️ Hochspannung</div>`;
            }}
            vCellEl.textContent = v.toFixed(3) + ' V';
            vCellEl.style.color = color;
            vCellEl.className = `cell-v ${{cls}}`;
            if (warnCellEl.innerHTML !== ico) warnCellEl.innerHTML = ico;
        }});

        const now = Date.now();
        const lastDs = mpptChart.data.datasets[0].data;
        const lastTs = lastDs.length > 0 ? lastDs[lastDs.length - 1].x : 0;
        if (now - lastTs > 55000 || lastDs.length === 0) {{
            mpptChart.data.datasets[0].data.push({{ x: now, y: data.pv_power || 0 }});
            mpptChart.data.datasets[1].data.push({{ x: now, y: data.pv_voltage || 0 }});
            const p = data.battery_power || 0;
            mpptChart.data.datasets[2].data.push({{ x: now, y: p < 0 ? Math.abs(p) : 0 }});
            mpptChart.data.datasets[3].data.push({{ x: now, y: p > 0 ? p : 0 }});
            cleanupChartData();
            mpptChart.update('none');
        }}
        setTimeout(updateData, 2000);
    }})
    .catch(() => {{
        document.getElementById('status-text').textContent = 'Verbindung verloren ⚠️';
        document.getElementById('status-toast').className = 'status-toast show error';
        setTimeout(updateData, 2000);
    }});
}}

// ── Tasmota Steuerung ──
const tasmotaIps = {tasmota_ips_json};
const tasmotaContainer = document.getElementById('tasmota-container');

function createTasmotaButtons() {{
            tasmotaIps.forEach((ip, idx) => {{
                const btn = document.createElement('button');
                btn.className = 'tasmota-circle';
                // JS-Template-Strings nutzen Backticks. 
                // idx ist eine JS-Variable, daher ${{idx}}
                btn.id = `tasmota-btn-${{idx}}`; 
                btn.dataset.ip = ip;
                btn.textContent = '...';
                btn.title = 'Lade...';
                btn.style.top = `${{75 + idx * 60}}px`;

                tasmotaContainer.appendChild(btn);

                setTimeout(() => {{
                    loadTasmotaName(ip, btn);
                    updateTasmotaStatus(idx);
                }}, idx * 300);

                btn.onclick = async (e) => {{
                    e.stopPropagation();
                    btn.textContent = "...";
                    try {{
                        await fetch(`/tasmota/${{ip}}?cmd=Power%20Toggle`);
                        setTimeout(() => updateTasmotaStatus(idx), 800);
                    }} catch (err) {{
                        showToast(`Umschalten fehlgeschlagen – ${{ip}}`, 'error');
                        setTimeout(() => updateTasmotaStatus(idx), 1500);
                    }}
                }};
            }});
        }}

        async function loadTasmotaName(ip, btn, retryCount = 0) {{
            try {{
                const r = await fetch(`/tasmota/${{ip}}?cmd=FriendlyName1`);
                if (!r.ok) throw new Error("Offline");
                const data = await r.json();
                let name = data.FriendlyName1?.trim() || ip;
                if (!name.trim() || name === "Offline") name = ip;
                btn.title = name;
            }} catch (err) {{
                if (retryCount < 3) {{
                    console.log(`Retry Name für ${{ip}} (${{retryCount + 1}}/3)`);
                    setTimeout(() => loadTasmotaName(ip, btn, retryCount + 1), 3000);
                }} else {{
                    btn.title = `Offline – ${{ip}}`;
                }}
            }}
        }}

async function updateTasmotaStatus(idx) {{
    const ip = tasmotaIps[idx];
    const btn = document.getElementById(`tasmota-btn-${{idx}}`);
    if (!btn) return;

    try {{
        const r = await fetch(`/tasmota/${{ip}}?cmd=Power`, {{
            cache: 'no-store',          // Cache umgehen
            headers: {{ 'Cache-Control': 'no-cache' }}
        }});

        if (!r.ok) throw new Error(`HTTP ${{r.status}}`);

        const data = await r.json();

        if (data && data.POWER) {{
            const state = data.POWER.trim().toUpperCase();
            btn.textContent = state;
            btn.classList.toggle('tasmota-on', state === "ON");
            console.log(`Tasmota ${{ip}} → ${{state}}`); // für Debugging im Console
        }} else {{
            throw new Error("Keine POWER-Antwort");
        }}
    }} catch (err) {{
        console.error(`Fehler bei Status-Abfrage ${{ip}}:`, err);
        btn.textContent = "ERR";
        btn.classList.remove('tasmota-on');
    }}
}}

function showToast(msg, type = 'error') {{
    const toast = document.getElementById('status-toast');
    const txt = document.getElementById('status-text');
    txt.textContent = msg;
    toast.className = `status-toast show ${{type === 'error' ? 'error' : ''}}`;
    setTimeout(() => toast.className = 'status-toast', 4000);
}}

// Tasmota-Buttons initialisieren
createTasmotaButtons();
setInterval(() => {{
    tasmotaIps.forEach((_, i) => updateTasmotaStatus(i));
}}, 60000);

// Namen alle 5 Minuten aktualisieren
setInterval(() => {{
    tasmotaIps.forEach((ip, i) => {{
        const btn = document.getElementById(`tasmota-btn-${{i}}`);
        if (btn) loadTasmotaName(ip, btn);
    }});
}}, 300000);
updateData();
</script>
</body>
</html>"""

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

httpd = None

def start_server():
    global httpd, server_start_time
    server_start_time = datetime.now()
    discover_dbus_services()
    threading.Thread(target=history_autosaver, daemon=True).start()
    threading.Thread(target=dbus_poller,       daemon=True).start()
    threading.Thread(target=collect_data,      daemon=True).start()
    log_message(f"Server gestartet auf Port {PORT}")
    print(f"JK-BMS Dashboard v{VERSION} → http://<IP>:{PORT}")
    httpd = ThreadedHTTPServer(('', PORT), BMSHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        save_history(backup=True)

def signal_handler(sig, frame):
    log_message(f"Signal {sig} – Erstelle permanentes Backup in /data...")
    save_history(backup=True)
    if httpd:
        httpd.shutdown()
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    load_history()
    cleanup_old_data()
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    try:
        loop = GLib.MainLoop()
        loop.run()
    except:
        pass
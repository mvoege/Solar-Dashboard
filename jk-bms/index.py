#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# index.py – Login-Schutz für Solar Dashboard (Zentrale DYN_NAME Konfiguration)

import http.server
import socketserver
import urllib.request
import urllib.parse
import time
import os
import sys
import signal
import subprocess
from collections import defaultdict
from datetime import datetime

# ====================================== KONFIGURATION =======================================
PORT = 1305
DASHBOARD_URL = "http://192.168.0.10:99"
DYN_NAME = "solar.ddnss.eu"  # <--- Zentrale Domain-Einstellung

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(SCRIPT_DIR, "pw.txt")
LOG_FILE = "/tmp/Solar_login.log"
failed_attempts = defaultdict(lambda: [0, 0])   # [count, last_attempt_timestamp]
BLOCK_THRESHOLD = 5                             # Fehlversuche bis Sperre
BLOCK_DURATION = 30 * 60                        # Sperrdauer in Sekunden (30 Min)

ALLOWED_USERS = {}

# ==========================================================================================

def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")
    except:
        pass

def load_users():
    global ALLOWED_USERS
    ALLOWED_USERS.clear()
    if not os.path.isfile(USERS_FILE):
        log_message("FEHLER: pw.txt nicht gefunden")
        return False
    try:
        with open(USERS_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or ':' not in line:
                    continue
                username, password = line.split(':', 1)
                ALLOWED_USERS[username.strip()] = password.strip()
        return True
    except Exception as e:
        log_message(f"Fehler beim Lesen von pw.txt: {e}")
        return False

def get_running_pids():
    pids = set()
    try:
        for proc_dir in os.listdir('/proc'):
            if proc_dir.isdigit():
                cmd_path = f'/proc/{proc_dir}/cmdline'
                if os.path.exists(cmd_path):
                    with open(cmd_path, 'r') as f:
                        cmd = f.read().replace('\x00', ' ')
                    if 'index.py' in cmd and 'run_server' in cmd:
                        pids.add(int(proc_dir))
    except: pass
    return list(pids)

# ====================== Management-Befehle ======================
if len(sys.argv) > 1:
    cmd = sys.argv[1].lower()
    if cmd == "start":
        if get_running_pids():
            print("Login-Server läuft bereits."); sys.exit(0)
        log_message("Server-Neustart")
        subprocess.Popen([sys.executable, __file__, "run_server"],
                         stdout=open(LOG_FILE, 'a', encoding='utf-8'),
                         stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(2)
        print(f"Gestartet -> http://{DYN_NAME}:{PORT}")
        sys.exit(0)
    elif cmd == "stop":
        for pid in get_running_pids():
            try: os.kill(pid, signal.SIGTERM)
            except: pass
        print("Gestoppt."); sys.exit(0)
    elif cmd == "restart":
        os.system(f'"{sys.executable}" "{__file__}" stop')
        time.sleep(1)
        os.system(f'"{sys.executable}" "{__file__}" start')
        sys.exit(0)

if len(sys.argv) == 1:
    os.execv(sys.executable, [sys.executable, __file__, "start"])

# ============================= HTTP Handler =============================
class LoginHandler(http.server.BaseHTTPRequestHandler):
    logged_in_ips = set()

    def get_client_ip(self):
        # Erkennt die echte IP hinter Nginx
        return self.headers.get('X-Real-IP', self.client_address[0])

    def is_host_allowed(self):
        host_header = self.headers.get('Host', '').lower().split(':')[0]
        client_ip = self.get_client_ip()
        
        # Erlaubte Hostnamen (basierend auf der zentralen Variable)
        allowed_hosts = [DYN_NAME.lower(), "localhost", "127.0.0.1"]
        
        if host_header in allowed_hosts or host_header.startswith("192.168."):
            return True
            
        failed_attempts[client_ip] = [BLOCK_THRESHOLD, time.time()]
        log_message(f"INST-BAN (Falscher Host): '{host_header}' von Real-IP {client_ip}")
        return False

    def log_message(self, format, *args):
        msg = format % args
        if "400" in msg or ("404" in msg and "favicon.ico" in msg):
            return
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), msg))

    def do_GET(self):
        client_ip = self.get_client_ip()
        attempts, last_time = failed_attempts[client_ip]
        
        if attempts >= BLOCK_THRESHOLD and (time.time() - last_time < BLOCK_DURATION):
            self.send_blocked_page(); return
            
        if not self.is_host_allowed():
            self.send_blocked_page(); return
        
        if self.path == "/site.webmanifest":
            self.send_manifest(); return

        if any(x in self.path for x in ["favicon", "apple-touch-icon", "android-chrome"]):
            icon_name = os.path.basename(self.path)
            icon_path = os.path.join(SCRIPT_DIR, "static", icon_name)
            if os.path.exists(icon_path):
                ext = os.path.splitext(icon_path)[1].lower()
                content_type = "image/png" if ext == ".png" else "image/x-icon"
                self.send_response(200)
                self.send_header('Content-type', content_type)
                self.end_headers()
                with open(icon_path, 'rb') as f: self.wfile.write(f.read())
                return

        if client_ip in self.logged_in_ips:
            self.proxy_to_dashboard()
        else:
            self.send_login_page()

    def do_POST(self):
        client_ip = self.get_client_ip()
        if not self.is_host_allowed():
            self.send_blocked_page(); return
            
        load_users()
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = self.rfile.read(length).decode('utf-8', errors='replace')
            params = urllib.parse.parse_qs(body)
            user = params.get('user', [''])[0].strip()
            pwd = params.get('pass', [''])[0]
        except: self.send_login_page("Anfragefehler"); return

        if user in ALLOWED_USERS and pwd == ALLOWED_USERS[user]:
            self.logged_in_ips.add(client_ip)
            failed_attempts[client_ip] = [0, 0]
            log_message(f"LOGIN ERFOLG: {user} ({client_ip})")
            self.send_response(302); self.send_header('Location', '/'); self.end_headers()
        else:
            failed_attempts[client_ip][0] += 1
            failed_attempts[client_ip][1] = time.time()
            count = failed_attempts[client_ip][0]
            log_message(f"LOGIN FEHLER: {user} ({client_ip}) - Versuch {count}/{BLOCK_THRESHOLD}")
            if count >= BLOCK_THRESHOLD: self.send_blocked_page()
            else: self.send_login_page(f"Falsch ({count}/{BLOCK_THRESHOLD})")

    def proxy_to_dashboard(self):
        if "favicon.ico" in self.path:
            self.send_error(404); return
        target = DASHBOARD_URL + self.path
        try:
            req = urllib.request.Request(target, method=self.command)
            for h in ['Accept', 'User-Agent', 'Content-Type', 'Referer']:
                if val := self.headers.get(h): req.add_header(h, val)
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.send_response(resp.code)
                for h, v in resp.getheaders():
                    if h.lower() not in ['content-length', 'connection', 'transfer-encoding']:
                        self.send_header(h, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except: self.send_error(502)

    def send_login_page(self, error=""):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('X-Robots-Tag', 'noindex, nofollow')
        self.end_headers()
        
        err_html = f'<div style="color:#ff6666;margin:15px 0;">{error}</div>' if error else ''
        html = f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
        <meta name="robots" content="noindex, nofollow">
        <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
        <title>Login</title>
        <link rel="manifest" href="/site.webmanifest">
        
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <meta name="apple-mobile-web-app-title" content="Solar">
        <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
        
        <meta name="theme-color" content="#000000">
        <style>
        body{{font-family:sans-serif;background:#000000;color:#fff;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}}
        .box{{background:#121212;padding:35px;border-radius:15px;width:100%;max-width:320px;text-align:center;box-shadow:0 10px 25px rgba(0,0,0,0.5);}}
        .ig{{position:relative;margin:10px 0;}}
        input{{width:100%;padding:12px;border-radius:8px;border:none;background:#3d3d3d;color:#fff;box-sizing:border-box;font-size:1rem;}}
        .toggle{{position:absolute;right:10px;top:50%;transform:translateY(-50%);cursor:pointer;font-size:1.2rem;user-select:none;color:#888;}}
        button{{width:100%;padding:14px;background:#007acc;color:#fff;border:none;border-radius:8px;cursor:pointer;margin-top:15px;font-size:1.1rem;}}
        </style></head><body><div class="box"><h2>Dashboard</h2>
        <form method="post"><div class="ig"><input type="text" name="user" placeholder="Benutzername" required></div>
        <div class="ig"><input type="password" name="pass" id="pw" placeholder="Passwort" required>
        <span class="toggle" id="tg">🙈</span></div><button type="submit">Anmelden</button></form>{err_html}</div>
        <script>const t=document.getElementById('tg'),p=document.getElementById('pw');
        t.onclick=()=>{{const s=p.type==='password';p.type=s?'text':'password';t.textContent=s?'🐵':'🙈';}};</script>
        </body></html>"""
        self.wfile.write(html.encode('utf-8'))

    def send_blocked_page(self):
        self.send_response(403); self.send_header('Content-type', 'text/html; charset=utf-8'); self.end_headers()
        self.wfile.write(b"<html><body style='background:#000000;color:#121212;text-align:center;padding-top:100px;'><h1>Gesperrt</h1></body></html>")

    def send_manifest(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/manifest+json')
        self.end_headers()
        manifest = """{
            "name": "Solar Dashboard",
            "short_name": "Solar",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#000000",
            "theme_color": "#000000",
            "icons": [
                {
                    "src": "/static/android-chrome-192x192.png",
                    "sizes": "192x192",
                    "type": "image/png"
                },
                {
                    "src": "/static/android-chrome-512x512.png",
                    "sizes": "512x512",
                    "type": "image/png"
                }
            ]
        }"""
        self.wfile.write(manifest.encode('utf-8'))

def start_server():
    socketserver.TCPServer.allow_reuse_address = True
    class T(socketserver.ThreadingMixIn, socketserver.TCPServer): daemon_threads = True
    with T(("0.0.0.0", PORT), LoginHandler) as h: h.serve_forever()

if 'run_server' in sys.argv:
    signal.signal(signal.SIGINT, lambda s,f: sys.exit(0))
    start_server()
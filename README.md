JK-BMS Web-Dashboard für Venus OS
Ein leichtgewichtiges, performantes Web-Dashboard für das JK-BMS, optimiert für Victron Venus OS Large (Raspberry Pi).
Diese Anwendung liest Daten über den D-Bus aus und visualisiert sie in Echtzeit in einem modernen Browser-Interface.

🌟 Features
Echtzeit-Monitoring: Visualisierung von SOC, Spannung, Stromstärke und Leistung.

Zell-Analyse: Detaillierte Ansicht aller Einzelzellspannungen inklusive Tages-Min/Max-Werten.

Historie & Charts: * 24h-Verlauf für PV-Leistung, Spannung und Hausverbrauch (via Chart.js).

30-Tage-Historie für Ertrag und Verbrauch.

Balancing-Indikator: Optische Hervorhebung aktiver Balancing-Vorgänge.

Tasmota-Integration: Direktes Schalten von Tasmota-Steckdosen über das Dashboard.

Smart Features:

Auto-Fullscreen & Wake-Lock: Verhindert das Abschalten des Displays (ideal für Wand-Tablets).

Dark/Light Mode: Anpassbares Design.

Warnsystem: Optische Alarme bei Überspannung oder niedrigem SOC.

🛠 Voraussetzungen
Venus OS (vorzugsweise "Large" Image).

Ein installierter D-Bus Treiber für das JK-BMS (z.B. venus-os_dbus-serialbattery).

Python 3 (auf Venus OS standardmäßig vorhanden).

🚀 Installation
Kopiere die Datei jk_bms_web.py auf deinen Venus OS GX (z.B. nach /data/apps/jk-bms/).

Mache das Skript ausführbar:

Bash
chmod +x jk_bms_web.py
⚙️ Konfiguration
Öffne die Datei und passe den Bereich WARNSTUFEN – HIER ANPASSEN an deine Systemwerte an:

Python
DASHBOARD_NAME = "Mein Energie-Zentrum"
MAX_PACK_VOLTAGE_WARNING = 29.40   # Beispiel für 8S LiFePO4
TASMOTA_IPS = ["192.168.0.14", "192.168.0.17"] # Deine Tasmota Geräte
PORT = 99                         # Port für das Web-Interface
📋 Bedienung
Das Skript verfügt über eine integrierte Prozess-Steuerung:

Starten: ./jk_bms_web.py start (startet im Hintergrund)

Stoppen: ./jk_bms_web.py stop

Status: ./jk_bms_web.py status

Neustart: ./jk_bms_web.py restart

Nach dem Start ist das Dashboard unter http://<deine-venus-ip>:99 erreichbar.

🗂 Verzeichnisse & Logging
Log-Datei: /var/volatile/tmp/jk_bms_dashboard.log

History-Cache: /var/volatile/tmp/bms_history.json (RAM-Disk für SD-Schonung)

Permanent-Backup: /data/apps/jk-bms/bms_history_backup.json (wird beim Beenden gesichert)

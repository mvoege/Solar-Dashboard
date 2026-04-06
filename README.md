Solar Web Dashboard für VenusOS 3.xx JK-BMS v24 and Victron Mppt 100/50

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

📋 Bedienung
Das Skript verfügt über eine integrierte Prozess-Steuerung:
Starten: ./jk_bms_web.py start (startet im Hintergrund)
Stoppen: ./jk_bms_web.py stop
Status: ./jk_bms_web.py status
Neustart: ./jk_bms_web.py restart

Verzeichnisse & Logging
Log-Datei: /var/volatile/tmp/jk_bms_dashboard.log
History-Cache: /var/volatile/tmp/bms_history.json (RAM-Disk für SD-Schonung)
Permanent-Backup: /data/apps/jk-bms/bms_history_backup.json (wird beim Beenden gesichert)

📱 Android & Tablet Optimierungen
Native Wake-Lock API:
Das Dashboard nutzt die navigator.wakeLock Schnittstelle, um das automatische Abschalten des Bildschirms zu verhindern, sobald der Vollbildmodus aktiviert wird.

Vollbild-Gestensteuerung:
Durch Tippen auf die Temperaturanzeige (oben links) wird der Fullscreen-Modus gewechselt, was besonders auf Tablets ohne physische Tasten nützlich ist.

Responsive Grid-Layout:
Das Design nutzt CSS-Grids, die sich automatisch von einer mehrspaltigen Desktop-Ansicht auf eine einspaltige, touch-optimierte Tablet-Ansicht anpassen.

Touch-optimierte Charts:
Die Chart.js-Konfiguration ist explizit für Touch-Events (touchstart, touchmove) optimiert, um eine flüssige Bedienung der Verlaufsanzeigen zu ermöglichen.

Dynamische Steuerelemente:
Buttons für Theme-Wechsel und Temperatur werden bei Inaktivität ausgeblendet, um ein sauberes "Kiosk-Design" zu gewährleisten, und erscheinen sofort bei Berührung des Bildschirms wieder.

Ressourcenschonung:
Die Daten werden im Hintergrund via AbortController mit Timeouts geladen, um auch bei älteren Tablet-Browsern oder instabilem WLAN keine Hänger zu verursachen.

<img width="1668" height="1143" alt="Screenshot" src="https://github.com/user-attachments/assets/aac59622-70d1-461f-928c-ec9091931419" />

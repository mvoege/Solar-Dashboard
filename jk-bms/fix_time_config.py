import os
import subprocess

def update_config():
    ntp_servers = "ptbtime1.ptb.de,ptbtime2.ptb.de,0.de.pool.ntp.org"
    config_path = "/etc/connman/main.conf"
    target_zone = "/usr/share/zoneinfo/Europe/Berlin"
    
    # 1. Zeitzone korrigieren
    # Wir prüfen, ob der Link existiert und korrekt auf Berlin zeigt
    try:
        current_link = os.readlink("/etc/localtime") if os.path.islink("/etc/localtime") else ""
        if current_link != target_zone:
            os.system(f"ln -sf {target_zone} /etc/localtime")
    except OSError:
        os.system(f"ln -sf {target_zone} /etc/localtime")

    # 2. NTP Config in /etc/connman/main.conf prüfen/schreiben
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            lines = f.readlines()
        
        updated = False
        new_lines = []
        
        # Wir prüfen jede Zeile. Wenn die Fallback-Server nicht passen, ersetzen wir sie.
        for line in lines:
            if line.startswith("FallbackTimeservers="):
                # Nur aktualisieren, wenn die gewünschten Server noch nicht drin stehen
                if ntp_servers not in line:
                    new_lines.append(f"FallbackTimeservers={ntp_servers}\n")
                    updated = True
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        
        # Falls die Zeile "FallbackTimeservers" komplett fehlte (unwahrscheinlich, aber sicher ist sicher)
        if not any(l.startswith("FallbackTimeservers=") for l in new_lines):
            new_lines.append(f"FallbackTimeservers={ntp_servers}\n")
            updated = True
        
        if updated:
            with open(config_path, "w") as f:
                f.writelines(new_lines)
            
            # 3. ConnMan neu starten (Der Weg, der bei dir funktioniert hat)
            # Nutzt den absoluten Pfad für die Ausführung beim Booten
            os.system("/etc/init.d/connman restart")

if __name__ == "__main__":
    update_config()

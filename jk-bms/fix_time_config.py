import os

def update_config():
    ntp_servers = "0.de.pool.ntp.org,1.de.pool.ntp.org,time.google.com"
    config_path = "/etc/connman/main.conf"
    
    # Zeitzone permanent auf Berlin setzen
    if os.readlink("/etc/localtime") != "/usr/share/zoneinfo/Europe/Berlin":
        os.system("ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime")

    # Prüfen, ob NTP-Server in der Config stehen
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            content = f.read()
        
        if ntp_servers not in content:
            # Hier nutzen wir einen f-String für die neue Zeile
            new_line = f"FallbackTimeservers={ntp_servers}\n"
            
            # Simpler Austausch: Wir hängen es ans Ende von [General], 
            # falls nicht vorhanden, oder überschreiben die Datei
            lines = content.splitlines()
            updated_lines = []
            found_ntp = False
            
            for line in lines:
                if line.startswith("FallbackTimeservers="):
                    updated_lines.append(new_line.strip())
                    found_ntp = True
                else:
                    updated_lines.append(line)
            
            if not found_ntp:
                updated_lines.append(new_line.strip())

            with open(config_path, "w") as f:
                f.write("\n".join(updated_lines))
            
            os.system("systemctl restart connman")

if __name__ == "__main__":
    update_config()

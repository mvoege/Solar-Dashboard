import os

def update_config():
    # Konfiguration
    ntp_servers = "ptbtime1.ptb.de,ptbtime2.ptb.de,0.de.pool.ntp.org"
    config_path = "/etc/connman/main.conf"
    target_zone = "/usr/share/zoneinfo/Europe/Berlin"
    
    print("Starte Konfigurations-Check...")

    # --- 1. ZEITZONE PRÜFEN ---
    is_zone_correct = False
    if os.path.islink("/etc/localtime"):
        if os.path.realpath("/etc/localtime") == target_zone:
            is_zone_correct = True

    if not is_zone_correct:
        print(f"Update: Setze Zeitzone auf {target_zone}")
        os.system("mount -o remount,rw /")
        os.system("rm -f /etc/localtime")
        os.system(f"ln -s {target_zone} /etc/localtime")
    else:
        print("Zeitzone ist bereits korrekt.")

    # --- 2. NTP CONFIG PRÜFEN ---
    updated_ntp = False
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            lines = f.readlines()
        
        new_lines = []
        found_key = False
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("FallbackTimeservers="):
                found_key = True
                current_value = stripped.split("=", 1)[1]
                if current_value != ntp_servers:
                    new_lines.append(f"FallbackTimeservers={ntp_servers}\n")
                    updated_ntp = True
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        
        if not found_key:
            new_lines.append(f"FallbackTimeservers={ntp_servers}\n")
            updated_ntp = True

        if updated_ntp:
            print(f"Update: Aktualisiere NTP Server in {config_path}")
            os.system("mount -o remount,rw /")
            with open(config_path, "w") as f:
                f.writelines(new_lines)
            
            # ConnMan neu starten, um Config zu laden
            os.system("/etc/init.d/connman restart")
        else:
            print("NTP Konfiguration ist bereits korrekt.")

    # --- 3. ZEIT-SYNCHRONISATION ERZWINGEN ---
    # Falls wir etwas geändert haben oder die Zeit manuell triggern wollen
    if not is_zone_correct or updated_ntp:
        print("Erzwinge Zeit-Synchronisation via BusyBox...")
        # Nutzt den ersten Server aus der Liste für einen schnellen Sync
        first_server = ntp_servers.split(',')[0]
        os.system(f"busybox ntpd -q -p {first_server}")

    # --- 4. DATEISYSTEM ABSICHERN ---
    # Wir setzen es am Ende immer sicherheitshalber auf RO
    os.system("mount -o remount,ro /")
    print("Vorgang abgeschlossen. Dateisystem ist wieder Read-Only.")

if __name__ == "__main__":
    update_config()
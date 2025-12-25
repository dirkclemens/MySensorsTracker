# Over-The-Air (OTA) Firmware Updates

## Übersicht

Das MySensorsTracker-Projekt unterstützt Over-The-Air (OTA) Firmware Updates für MySensors-Nodes. Dies ermöglicht es, die Firmware Ihrer Sensor-Nodes zu aktualisieren, ohne physischen Zugriff auf die Geräte zu haben.

## Voraussetzungen

### Hardware-Anforderungen

Ihre MySensors-Nodes müssen einen kompatiblen Bootloader haben:

- **MYSBootloader** - Empfohlen, speziell für MySensors entwickelt
- **DualOptiboot** - Alternative für ATmega328p Boards

Standard Arduino Optiboot-Bootloader unterstützt **KEIN** OTA!

### Software-Anforderungen

- Python-Abhängigkeiten (bereits installiert):
  - `pymysensors` >= 0.26.0
  - `intelhex` >= 2.3.0
  - `crcmod` >= 1.7

- MySensors Gateway:
  - Ethernet Gateway auf `192.168.2.211:5003`
  - Funkverbindung zu den Nodes

## Bootloader Installation

### MYSBootloader Flashen

MYSBootloader ist der empfohlene Bootloader für MySensors OTA-Updates.

#### Download und Vorbereitung

1. **MYSBootloader herunterladen:**
   - Forum: https://forum.mysensors.org/topic/3453/mysbootloader-1-3-pre-release
   - GitHub: https://github.com/mysensors/MySensorsBootloaderRF24

2. **Unterstützte Mikrocontroller:**
   - ATmega328p (Arduino Pro Mini, Nano)
   - ATmega168
   - ATmega1284p
   - ATmega644p
   - ATmega2560 (Arduino Mega)

#### ISP Programmer flashen

**Für Arduino Pro Mini 3.3V 8MHz (ATmega328p):**

```bash
# Bootloader flashen
avrdude -p atmega328p -c usbasp -U flash:w:MYSBootloader_328p_8MHz.hex:i

# Fuses setzen
avrdude -p atmega328p -c usbasp -U lfuse:w:0xE2:m -U hfuse:w:0xDA:m -U efuse:w:0xFE:m
```

**Für Arduino Pro Mini 5V 16MHz (ATmega328p):**

```bash
# Bootloader flashen
avrdude -p atmega328p -c usbasp -U flash:w:MYSBootloader_328p_16MHz.hex:i

# Fuses setzen
avrdude -p atmega328p -c usbasp -U lfuse:w:0xFF:m -U hfuse:w:0xDA:m -U efuse:w:0xFE:m
```

**Für ATmega1284p:**

```bash
# Bootloader flashen
avrdude -p atmega1284p -c usbasp -U flash:w:MYSBootloader_1284p.hex:i

# Fuses setzen
avrdude -p atmega1284p -c usbasp -U lfuse:w:0xFF:m -U hfuse:w:0xDC:m -U efuse:w:0xFE:m
```

#### MYSController für Bootloader-Flash (Alternative)

Sie können auch MYSController verwenden, um den Bootloader zu flashen:

1. **MYSController starten**
2. **Actions → Firmware → Upload**
3. **Wählen Sie den richtigen Mikrocontroller**
4. **Wählen Sie "Bootloader" als Firmware-Typ**
5. **Flashen über ISP Programmer**

### DualOptiboot Flashen (Alternative)

Falls Sie DualOptiboot bevorzugen:

1. **Bootloader herunterladen:**
   ```bash
   git clone https://github.com/mysensors/DualOptiboot.git
   cd DualOptiboot
   ```

2. **Mit ISP Programmer flashen:**
   ```bash
   # Für Arduino Pro Mini 3.3V 8MHz
   avrdude -p atmega328p -c usbasp -U flash:w:optiboot_flash_atmega328p_UART0_38400_8000000L.hex:i -U lock:w:0x0F:m
   
   # Für Arduino Pro Mini 5V 16MHz
   avrdude -p atmega328p -c usbasp -U flash:w:optiboot_flash_atmega328p_UART0_115200_16000000L.hex:i -U lock:w:0x0F:m
   ```

3. **Fuses setzen:**
   ```bash
   # 3.3V 8MHz
   avrdude -p atmega328p -c usbasp -U lfuse:w:0xE2:m -U hfuse:w:0xDA:m -U efuse:w:0xFE:m
   
   # 5V 16MHz  
   avrdude -p atmega328p -c usbasp -U lfuse:w:0xFF:m -U hfuse:w:0xDA:m -U efuse:w:0xFE:m
   ```

## Node-Sketch Vorbereitung

Ihr MySensors-Sketch muss OTA-Unterstützung aktivieren:

```cpp
// Vor #include <MySensors.h>
#define MY_OTA_FIRMWARE_FEATURE

// Optional: Firmware Typ und Version definieren
#define MY_OTA_FIRMWARE_TYPE 1
#define MY_OTA_FIRMWARE_VERSION 1

#include <MySensors.h>

void setup() {
  // Ihr Setup-Code
}

void loop() {
  // Ihr Loop-Code
}
```

## Firmware kompilieren

1. **In Arduino IDE:**
   - Sketch > Export compiled Binary (Ctrl+Alt+S)
   - Oder: Preferences > "Show verbose output during compilation" aktivieren

2. **Hex-Datei finden:**
   - Im Sketch-Ordner wird eine `.hex` Datei erstellt
   - Beispiel: `/tmp/arduino_build_123456/MySketch.ino.hex`

3. **Alternative - PlatformIO:**
   ```bash
   pio run
   # Hex-Datei: .pio/build/<env>/firmware.hex
   ```

## OTA Update durchführen

### Schritt 1: Firmware hochladen

1. Öffnen Sie die Web-UI: `http://<server-ip>:5555`
2. Klicken Sie in der Navigation auf **"OTA"**
3. Im Bereich **"Upload New Firmware"**:
   - **Firmware Type:** Geben Sie eine Nummer ein (z.B. `1` für Sensor-Typ)
   - **Firmware Version:** Versionsnummer (z.B. `2` für Version 2)
   - **Firmware File:** Wählen Sie die `.hex` Datei
4. Klicken Sie auf **"Upload Firmware"**

Die Firmware wird geladen und validiert. Sie sehen dann:
- Anzahl der Blöcke (Blocks)
- CRC-Prüfsumme (zur Validierung)

### Schritt 2: Node für Update auswählen

1. In der Tabelle **"Nodes - Schedule OTA Update"** sehen Sie alle Ihre Nodes
2. Für den gewünschten Node:
   - Wählen Sie **Firmware Type** aus der Dropdown-Liste
   - Wählen Sie **Firmware Version** aus der Dropdown-Liste
   - Klicken Sie auf **"Schedule Update"**

Der Node ist jetzt für das Update vorgemerkt. Status: **"requested"**

### Schritt 3: Automatischer Update-Prozess

Der folgende Prozess läuft automatisch ab:

1. **Reboot Request (I_REBOOT)**
   - Beim nächsten Heartbeat des Nodes
   - Node erhält Reboot-Kommando
   - Node startet neu

2. **Firmware Config Request (ST_FIRMWARE_CONFIG_REQUEST)**
   - Node sendet beim Start seine aktuelle Firmware-Info
   - Server antwortet mit neuer Firmware-Info
   - Status: **"unstarted"**

3. **Firmware Block Transfer (ST_FIRMWARE_REQUEST/RESPONSE)**
   - Node fordert Firmware-Blöcke an (16 Bytes pro Block)
   - MYSBootloader oder DualOptiboot flashen (siehe oben)
   - Prüfen Sie die Bootloader-Version im Log (bei Config Request
   - Fortschritt wird geloggt
   - Status: **"started"**

4. **Verification & Flash**
   - Node prüft CRC-Summe
   - Bei Erfolg: Neue Firmware wird geladen
   - Bei Fehler: Alte Firmware bleibt aktiv

5. **Neustart**
   - Node startet mit neuer Firmware
   - Update ist abgeschlossen

## Logs überwachen

ÖfBei MYSBootloader: Node wartet auf Firmware
- Senden Sie die Firmware erneut
- Bei DualOptiboot: Watchdog Timer nicht richtig konfiguriert
- Notfalls: Node manuell neu flashen via ISP

## MYSBootloader vs DualOptiboot

### MYSBootloader (Empfohlen)

**Vorteile:**
- Speziell für MySensors entwickelt
- Kleiner Footprint (ca. 512 bytes)
- Unterstützt mehr Mikrocontroller
- Integrierter Watchdog
- Bessere Fehlerbehandlung

**Nachteile:**
- Weniger verbreitet als DualOptiboot
- Benötigt MYSController für einfaches Flashen

### DualOptiboot

**Vorteile:**
- Weit verbreitet in der Community
- Gut dokumentiert
- Zwei unabhängige Flash-Bereiche

**Nachteile:**
- Größerer Bootloader (ca. 1KB)
- Nur für ATmega328p optimiert
- Watchdog-Probleme bei manchen Boards
```bash
cd /Users/dirk/Projekte/python/MySensorsTracker
venv/bin/python app.py
```

Typische Log-Ausgaben:

```
[INFO] Node 5 requested for firmware update: type 1 version 2
[INFO] Sent reboot request to node 5 for firmware update
[INFO] Node 5 firmware config request: type 1 ver 1 blocks 128 CRC 5A3F bootloader 1
[INFO] Node 5 updating from type 1 ver 1 to type 1 ver 2
[DEBUG] Node 5 sending block 0/255
[DEBUG] Node 5 sending block 1/255
...
[DEBUG] Node 5 sending block 255/255
```

## Troubleshooting

### Problem: Node reagiert nicht auf Update

**Lösung:**
- Prüfen Sie, ob der Node online ist und Heartbeats sendet
- Starten Sie den Node manuell neu (Reset-Taste)
- Prüfen Sie die Funkverbindung zum Gateway

### Problem: "No firmware loaded" Fehler

**Lösung:**
- Firmware muss zuerst hochgeladen werden (Schritt 1)
- Prüfen Sie, ob Type und Version korrekt sind

### Problem: Update schlägt fehl, Node läuft noch mit alter Firmware

**Mögliche Ursachen:**

1. **CRC-Fehler:**
   - Funkverbindung gestört
   - Blocks wurden fehlerhaft übertragen
   - Lösung: Update wiederholen

2. **Falscher Bootloader:**
   - Standard Optiboot unterstützt kein OTA
   - DualOptiboot flashen (siehe oben)

3. **Zu wenig Flash-Speicher:**
   - Neue Firmware zu groß
   - Sketch optimieren oder Funktionen entfernen

### Problem: "Firmware not valid" beim Upload

**Lösung:**
- Nur `.hex` Dateien (Intel HEX Format)
- KMYSBootloader Forum Thread](https://forum.mysensors.org/topic/3453/mysbootloader-1-3-pre-release)
- [MYSBootloader GitHub](https://github.com/mysensors/MySensorsBootloaderRF24)
- [DualOptiboot Repository](https://github.com/mysensors/DualOptiboot)
- [pymysensors Library](https://github.com/theolind/pymysensors)
- [MYSController](https://github.com/mysensors/MYSController

### Problem: Node bleibt im Bootloader hängen

**Lösung:**
- Watchdog Timer wurde nicht richtig konfiguriert
- Node manuell neu flashen via ISP

## Fortgeschrittene Nutzung

### Mehrere Firmware-Versionen verwalten

Sie können mehrere Firmware-Versionen gleichzeitig geladen haben:

- Type 1, Version 1: Temperatur-Sensor (alte Version)
- Type 1, Version 2: Temperatur-Sensor (neue Version)
- Type 2, Version 1: Bewegungsmelder

Dies ermöglicht gezielte Updates einzelner Node-Typen.

### Batch-Updates

Für Updates mehrerer Nodes:

1. Laden Sie die Firmware einmal hoch
2. Planen Sie Updates für alle gewünschten Nodes
3. Nodes werden nacheinander aktualisiert

### Firmware-Typen

Organisieren Sie Ihre Firmware mit Type-Nummern:

- **Type 1:** Temperatur/Luftfeuchtigkeitssensoren
- **Type 2:** Bewegungsmelder
- **Type 3:** Türsensoren
- **Type 10:** Repeater-Nodes

Definieren Sie diese in Ihrem Sketch:

```cpp
#define MY_OTA_FIRMWARE_TYPE 1  // Temperatur-Sensor
#define MY_OTA_FIRMWARE_VERSION 3
```

## MySensors Message Format

Für Entwickler - die OTA-Kommunikation verwendet folgendes Format:

```
node_id;child_id;command;ack;type;payload
```

**Firmware Config Request:**
```
5;255;4;0;0;01000100800016E80300
```
- Command 4: C_STREAM
- Type 0: ST_FIRMWARE_CONFIG_REQUEST
- Payload: fw_type(0100), fw_ver(0100), blocks(8000), crc(16E8), bootloader(0300)

**Firmware Config Response:**
```
5;255;4;0;1;010002000080A5B3
```
- Type 1: ST_FIRMWARE_CONFIG_RESPONSE
- Payload: fw_type(0100), fw_ver(0200), blocks(0080), crc(A5B3)

**Firmware Block Request:**
```
5;255;4;0;2;0100020000000
```
- Type 2: ST_FIRMWARE_REQUEST  
- Payload: fw_type(0100), fw_ver(0200), block(0000)

**Firmware Block Response:**
```
5;255;4;0;3;0100020000000C94AC030C9491240C94B8240C94D403...
```
- Type 3: ST_FIRMWARE_RESPONSE
- Payload: fw_type(0100), fw_ver(0200), block(0000), data (16 bytes hex)

## Sicherheit

- OTA-Updates erfolgen unverschlüsselt über das MySensors-Netzwerk
- Stellen Sie sicher, dass nur Sie Zugriff auf die Web-UI haben
- Bei kritischen Installationen: Netzwerk absichern (Firewall, VPN)

## Referenzen

- [MySensors OTA Documentation](https://www.mysensors.org/about/fota)
- [DualOptiboot Repository](https://github.com/mysensors/DualOptiboot)
- [pymysensors Library](https://github.com/theolind/pymysensors)

## Lizenz

Diese OTA-Implementation basiert auf:
- pymysensors (MIT License)
- MySensors Framework (GPL v2)
- DualOptiboot (GPL v2)

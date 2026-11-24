# kol-ble-pairing — BLE pairing for the KOL Smart Badge (stock firmware)

CLI that pairs a badge running the **stock firmware** (not v2) over BLE on Linux, macOS and Windows.

It is made for a production line: you give the Wi-Fi credentials **once per session**, then the tool pairs one badge after another until you press Ctrl-C.

What it does:

1. Gets the Wi-Fi **SSID** and **password**, once. It does not start without both (see *Wi-Fi credentials* below).
2. Asks the operator to put a badge in **pairing mode**, then waits for a badge advertising as `SCMD…`/`SMCD…`. If more than one badge is in pairing mode at the same time, it refuses to choose one and scans again 5 s later. Badges already paired in this session are ignored.
3. Opens a secure BluFi session (DH + AES-128-CFB + CRC16) and reads the badge's config.
4. Sends the fixed settings with vendor command `02 12` and reads them back. All other settings stay as they were on the badge.
5. Sends the SSID and password, then has the badge scan for Wi-Fi networks. If the SSID is not in the scan, it prints the networks the badge sees and stops.
6. Has the badge join the network and polls its Wi-Fi status until it reports a connection. It then prints `==== SUCCESS: <badge> ... ====` and goes back to step 2 for the next badge.

If a badge fails, the tool prints `!!!! FAILED: <badge>: <reason>` and tells you to check that badge's settings by hand, then moves on to the next badge.

Press Ctrl-C to end the session (SIGTERM, SIGHUP and Ctrl-Break on Windows do the same). If a badge is connected at that moment, the tool says its pairing did not finish and that you should check its settings by hand. It then prints a summary of the session: how many badges were paired, how many need checking and how many failed.

`--once` pairs a single badge and exits. It waits for Enter before scanning and gives up after 120 s.

## Fixed parameters

| Setting | Value | BLE field (`BlufiMsg.NVS`) |
|---|---|---|
| API server (config, record, log) | `badgeapi.yourkol.ai` | `#1`, `#2`, `#3` |
| Segment duration | `600` s | `#4 record_time` |
| Timezone | GMT-3, sent as `UTC+3` | `#8 timezone` |

The badge takes a POSIX TZ string, and POSIX flips the sign. The stock default `UTC-8` means China time (UTC+08:00), so GMT-3 has to be sent as `UTC+3`. To change it, edit `TIMEZONE` at the top of `kol_ble_pairing.py`.

These constants live at the top of `kol_ble_pairing.py`.

## Standalone executable (no Python on the station)

The production stations do not need Python. Build a single executable once per OS, on a build machine that does have Python 3.10+, and copy just that file to the stations:

| OS | Build command | Result |
|---|---|---|
| Linux / macOS | `./build.sh` | `dist/kol-ble-pairing` |
| Windows | `powershell -ExecutionPolicy Bypass -File build.ps1` | `dist\kol-ble-pairing.exe` |

PyInstaller does not cross-compile, so each OS has to be built on that OS. On the station:

```sh
./kol-ble-pairing --wifi-file line3.wifi          # Windows: kol-ble-pairing.exe --wifi-file line3.wifi
```

The OS notes under *Install* still apply: BlueZ on Linux, the Bluetooth permission on macOS, Windows 10 1709+.

- **Linux:** the executable needs a glibc at least as new as the build machine's. Build on the oldest distro the stations run.
- **macOS:** an unsigned binary copied from another Mac is quarantined by Gatekeeper. Clear it with `xattr -d com.apple.quarantine kol-ble-pairing`, or sign it.
- **Windows:** antivirus tools sometimes flag single-file PyInstaller executables. Sign the `.exe`, or whitelist it on the stations.

## Install (running from source)

Python 3.10 or newer is required (3.11+ is recommended for clean Ctrl-C handling on Windows).

```sh
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

- **Linux:** needs BlueZ, with the adapter powered on (`bluetoothctl power on`). The user must be able to reach BlueZ over D-Bus, which works by default on desktop distros.
- **macOS:** the first run asks for Bluetooth permission for the terminal app. Allow it in System Settings → Privacy & Security → Bluetooth. Badges show up under a per-Mac UUID instead of a MAC address, which is expected.
- **Windows:** needs Windows 10 1709 or newer and a BLE-capable adapter. You do not need to pair the badge in Windows Bluetooth settings first.

## Wi-Fi credentials

Sources, in order of precedence:

1. `--ssid` / `--password`. The password stays visible in the shell history and the process list, so avoid this on shared stations.
2. `--wifi-file FILE`: a text file with one setting per line (lines starting with `#` are ignored):

   ```text
   # line 3
   ssid=Factory WiFi
   password=the password
   ```

   Everything after the first `=` is the value, spaces included. Make the file readable only by the operator account (`chmod 600` on Linux and macOS). `*.wifi` files are git-ignored, so credentials are not committed by mistake.
3. The environment variables `KOL_WIFI_SSID` and `KOL_WIFI_PASSWORD`.
4. A prompt at start-up (the password is hidden). It is asked once for the whole session.

## Usage

```sh
python kol_ble_pairing.py --wifi-file line3.wifi              # production line
python kol_ble_pairing.py --ssid "MyNetwork"                  # asks for the password once
python kol_ble_pairing.py --ssid "MyNetwork" --once           # a single badge
python kol_ble_pairing.py --wifi-file line3.wifi --once --name SMCD510011  # one specific badge
```

Exit codes: in production-line mode, `130` when the session is ended with Ctrl-C, or `1` on a Bluetooth adapter error. With `--once`:

- `0`: paired and connected.
- `1`: error.
- `2`: connected, but the config read back differs from what was sent.
- `130`: interrupted.

Protocol reference: `docs/BLE_PROVISIONING.md` and `docs/blufi.proto` in the ESP32 workspace.

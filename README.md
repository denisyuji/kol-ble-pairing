# kol-ble-pairing — pair KOL Smart Badges over Bluetooth

Sets up KOL Smart Badges (stock firmware) on a production line: it sends each badge the KOL server settings and the Wi-Fi credentials over Bluetooth, then checks that the badge joins the Wi-Fi. You enter the Wi-Fi credentials once, then pair one badge after another.

## Download

Get the file for your computer from the [latest release](https://github.com/denisyuji/kol-ble-pairing/releases/latest):

| Computer | File |
|---|---|
| Linux (Ubuntu 20.04 or newer) | `kol-ble-pairing-linux-x64.tar.gz` |
| Mac with Apple Silicon (M1 or newer) | `kol-ble-pairing-macos-arm64.tar.gz` |
| Windows 10 (1709 or newer) or 11 | `kol-ble-pairing-windows-x64.exe` |

No Python or other software is needed.

## Install and run

**Linux** (desktop distros already have Bluetooth support, BlueZ):

```sh
tar -xzf kol-ble-pairing-linux-x64.tar.gz
bluetoothctl power on
./kol-ble-pairing
```

**macOS**

```sh
tar -xzf kol-ble-pairing-macos-arm64.tar.gz
xattr -d com.apple.quarantine kol-ble-pairing   # allow the downloaded file to run
./kol-ble-pairing
```

The first run asks for Bluetooth access for Terminal: allow it (System Settings → Privacy & Security → Bluetooth).

**Windows**: open PowerShell in the download folder and run:

```powershell
.\kol-ble-pairing-windows-x64.exe
```

If SmartScreen or the antivirus blocks it, allow the file. You do not need to pair the badge in the Windows Bluetooth settings.

## Pairing badges

1. Start the tool. It asks for the Wi-Fi network name and password, once for the whole session.
2. Put a badge in **pairing mode**. The tool finds it, sets it up and prints one line per step.
3. It ends each badge with one of these lines:
   - `==== SUCCESS: SMCD510011 is paired and connected to '<network>' ====`: done, take the next badge.
   - `!!!! FAILED: SMCD510011: <reason>`: set up that badge again, or check its settings by hand. If the badge can't see the network, the tool lists the networks it does see.
4. Press **Ctrl-C** to stop. The tool prints how many badges were paired, how many need checking and how many failed.

Put only one badge in pairing mode at a time: when it sees two, it waits until only one is left. Badges already paired in the session are ignored. A pairing usually takes about 20 s.

### Options

| Option | Meaning |
|---|---|
| `--wifi-file FILE` | Read the Wi-Fi credentials from a file, so they don't have to be typed (see below). |
| `--ssid NAME`, `--password PASSWORD` | Give the credentials on the command line. The password stays in the shell history, so prefer `--wifi-file`. |
| `--name SMCD510011` | Pair only the badge with this name. |
| `--once` | Pair one badge and exit. |
| `--help` | List the options. |

A Wi-Fi file is a text file like this (keep it private, e.g. `chmod 600`):

```text
ssid=Factory WiFi
password=the password
```

The tool can also read the credentials from the environment variables `KOL_WIFI_SSID` and `KOL_WIFI_PASSWORD`.

### What it sets on the badge

| Setting | Value |
|---|---|
| API server (config, record, log) | `badgeapi.yourkol.ai` |
| Segment duration | 600 s |
| Timezone | GMT-3 |
| Wi-Fi | the network you gave |

All other badge settings stay as they were. "Connected" means the badge joined the Wi-Fi and got an IP address. The tool can't check over Bluetooth that the badge reaches the API server.

---

## For developers

### What the tool does

1. It waits for a badge advertising as `SCMD…`/`SMCD…` with a 10-character name (e.g. `SMCD510011`). Other name lengths are badge models that use a different command set, so it ignores them.
2. It opens a BluFi session (DH + AES-128-CFB + CRC16).
3. It reads the config, writes the fixed settings with vendor command `02 12` (`BlufiMsg.NVS`, encoded as the vendor app does it: text fields always sent, at most 255 bytes each and 1021 in total) and reads them back to verify.
4. It sends the SSID and password and asks the badge to join. It stops as soon as the badge reports a failure code (wrong password, network not found, association failed), and only then has the badge scan for networks.

The fixed values are constants at the top of `kol_ble_pairing.py`. The badge takes a POSIX TZ string with the sign flipped, so GMT-3 is sent as `UTC+3`. Protocol reference: `AGENTS.md` here, and `docs/BLE_PROVISIONING.md` and `docs/blufi.proto` in the ESP32 workspace.

Exit codes with `--once`: `0` paired and connected, `1` error, `2` connected but the config read back differs, `130` interrupted. In production-line mode: `130` on Ctrl-C, `1` on a Bluetooth adapter error.

### Running from source

Needs Python 3.10 or newer (3.11+ for clean Ctrl-C handling on Windows):

```sh
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python kol_ble_pairing.py --once --ssid "MyNetwork"
```

### Building the executables

PyInstaller doesn't cross-compile, so each OS builds its own executable into `dist/`:

| OS | Command |
|---|---|
| Linux | `./build-container.sh`: builds with podman in Debian bullseye (glibc 2.31), so the binary runs on Ubuntu 20.04 and newer. A plain `./build.sh` binary runs only on distros at least as new as the build machine. |
| macOS | `./build.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File build.ps1` |

### CI and releases

`.github/workflows/build.yml` builds all three on every push to `main`, on pull requests and on demand. The executables are the run's **Artifacts**. Pushing a tag such as `v1.0.1` also publishes them as a GitHub release.

`./fetch-artifacts.sh` pushes the current branch, waits for its CI run and downloads the three executables into `artifacts/`. It needs `gh`, logged in, and refuses to run with uncommitted changes. The macOS job sometimes waits for a runner until GitHub cancels it. Re-run it with `gh run rerun <run-id> --failed`.

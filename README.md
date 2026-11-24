# kol-ble-pairing — BLE pairing for the KOL Smart Badge (stock firmware)

CLI that pairs a badge running the **stock firmware** (not v2) over BLE on Linux, macOS and Windows.

It is made for a production line: you give the Wi-Fi credentials **once per session**, then the tool pairs one badge after another until you press Ctrl-C.

What it does:

1. Gets the Wi-Fi **SSID** and **password**, once. It does not start without both (see *Wi-Fi credentials* below).
2. Asks the operator to put a badge in **pairing mode**, then waits for a badge advertising as `SCMD…`/`SMCD…` with a 10-character name (e.g. `SMCD510011`). Other name lengths belong to badge models that the vendor app drives with a different command set, so the tool ignores them. If more than one badge is in pairing mode at the same time, it refuses to choose one and scans again 5 s later. Badges already paired in this session are ignored.
3. Opens a secure BluFi session (DH + AES-128-CFB + CRC16) and reads the badge's config.
4. Sends the fixed settings with vendor command `02 12` and reads them back. All other settings stay as they were on the badge. The message is encoded the way the vendor app does it: text fields are always sent, empty ones included, and the app's limits apply (255 bytes per text field, 1021 bytes in total).
5. Sends the SSID and password.
6. Has the badge join the network and polls its Wi-Fi status until it reports a connection. It stops as soon as the badge reports a wrong password, a network it cannot find, or a failed association (the codes the vendor app shows). Only when the join fails does it have the badge scan for Wi-Fi networks, to tell a network out of range (it then prints the networks the badge sees) from a wrong password. On success it prints `==== SUCCESS: <badge> ... ====` and goes back to step 2 for the next badge.

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
| Linux, in a container (recommended) | `./build-container.sh` | `dist/kol-ble-pairing` |

PyInstaller does not cross-compile, so each OS has to be built on that OS.

GitHub Actions (`.github/workflows/build.yml`) builds all three on every push to `main`, on pull requests, and on demand from the Actions tab. Download the executables from the run's **Artifacts**. Pushing a tag such as `v1.0.0` also publishes them as a GitHub release:

- `kol-ble-pairing-linux-x64.tar.gz`, built with `build-container.sh` (glibc 2.31, runs on Ubuntu 20.04 and newer).
- `kol-ble-pairing-macos-arm64.tar.gz`, for Apple Silicon.
- `kol-ble-pairing-windows-x64.exe`.

`./fetch-artifacts.sh` does this from the command line: it pushes the current branch, waits for its CI run and downloads the three files into `artifacts/` (git-ignored). It needs the GitHub CLI (`gh`), logged in, and refuses to run with uncommitted changes.

On the station:

```sh
./kol-ble-pairing --wifi-file line3.wifi          # Windows: kol-ble-pairing.exe --wifi-file line3.wifi
```

The OS notes under *Install* still apply: BlueZ on Linux, the Bluetooth permission on macOS, Windows 10 1709+.

- **Linux:** the executable needs a glibc at least as new as the build machine's. It does not depend on the kernel version. `./build-container.sh` builds it with podman in Debian bullseye (glibc 2.31, see `Containerfile`), so it runs on Ubuntu 20.04, Debian 11, RHEL 9 and newer, whichever distro it is built on. Only `dist/` is mounted writable, and the binary is owned by the user who ran the script. A plain `./build.sh` binary runs only on distros at least as new as the build machine.
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

With the standalone executable, use the same options with `./kol-ble-pairing` (Windows: `kol-ble-pairing.exe`) in place of `python kol_ble_pairing.py`. `--help` lists them.

| Option | Meaning |
|---|---|
| `--wifi-file FILE` | Read the SSID and password from `FILE` (see *Wi-Fi credentials*). |
| `--ssid SSID`, `--password PASSWORD` | Give the credentials on the command line. |
| `--name NAME` | Pair only the badge with this BLE name, e.g. `SMCD510011`. |
| `--once` | Pair one badge and exit, instead of badge after badge. |

The tool prints a timestamped line for every step, so the operator can follow it. A badge that pairs looks like this:

```text
[22:45:53] found badge SMCD510011 (80:B5:4E:5B:16:96, -55 dBm)
[22:45:54] step 1/5: connecting to SMCD510011 over BLE ...
[22:46:08] BLE connected (MTU 256); negotiating encryption (DH key exchange) ...
[22:46:10] secure BluFi session established (AES-128 + CRC16)
[22:46:10] step 2/5: reading the badge's current configuration ...
    domain_config   = 'badgeapi.yourkol.ai'  (was 'cw.gdszzy.com')
    ...
[22:46:10] step 3/5: sending server, segment duration and timezone ...
[22:46:10] the badge accepted the configuration; reading it back to verify ...
[22:46:11] configuration verified: the badge stored server, segment duration and timezone
[22:46:11] step 4/5: sending Wi-Fi credentials for 'Factory WiFi' ...
[22:46:11] Wi-Fi credentials sent (encrypted)
[22:46:11] step 5/5: asking the badge to join 'Factory WiFi' (up to 90 s) ...
[22:46:18] badge Wi-Fi: connected (0)
[22:46:18] the badge reports it is connected to 'Factory WiFi' after 7 s
[22:46:18] disconnecting from SMCD510011 ...

==== SUCCESS: SMCD510011 is paired and connected to 'Factory WiFi' ====
```

"Connected" means the badge joined the network and has an IP address. Nothing over BLE checks that it can reach the API server; its first request to the API confirms that.

Exit codes: in production-line mode, `130` when the session is ended with Ctrl-C, or `1` on a Bluetooth adapter error. With `--once`:

- `0`: paired and connected.
- `1`: error.
- `2`: connected, but the config read back differs from what was sent.
- `130`: interrupted.

Protocol reference: `docs/BLE_PROVISIONING.md` and `docs/blufi.proto` in the ESP32 workspace.

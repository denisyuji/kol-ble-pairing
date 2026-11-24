# AGENTS.md — kol-ble-pairing

In Markdown files, do not break lines in the middle of a sentence: write each paragraph and each list item on a single line and let the editor wrap it.

## What this is

A production-line CLI that pairs KOL Smart Badges running the **stock vendor firmware** (3.1.x, not the v2 Rust firmware) over BLE: it writes the fixed KOL settings (API server, segment duration, timezone) and the Wi-Fi credentials, then waits until the badge reports it is on Wi-Fi. `README.md` is the operator documentation (download, install, run) with a developer section at the bottom; keep it in step with the code (options, result lines, exit codes, build and CI). `README-pt-BR.md` is its Brazilian Portuguese translation. Always keep both READMEs in sync: every change to one goes into the other in the same commit.

## Files

| File | Content |
|---|---|
| `kol_ble_pairing.py` | The whole tool, a single file: BluFi client (DH, AES-128-CFB, CRC16, fragmentation), vendor `BD A0` framing, hand-rolled protobuf for `BlufiMsg.NVS`, pairing flow and CLI |
| `build.sh`, `build.ps1` | Build a single-file PyInstaller executable for the current OS into `dist/` |
| `build-container.sh`, `Containerfile` | Build the Linux executable with podman in Debian bullseye (glibc 2.31), bind-mounting only `dist/` |
| `fetch-artifacts.sh` | Pushes the current branch, waits for its CI run and downloads the Linux, macOS and Windows executables into `artifacts/` (needs `gh`) |
| `.github/workflows/build.yml` | CI build for Linux (via `build-container.sh`, glibc 2.31), macOS arm64 and Windows; a `v*` tag publishes a release |
| `requirements.txt`, `requirements-build.txt` | Runtime (`bleak`, `cryptography`) and build (`pyinstaller`) dependencies |

`build/`, `dist/`, `artifacts/`, `*.spec`, `.build-venv/` and `*.wifi` (credential files) are git-ignored.

## Running and building

- From source: `python kol_ble_pairing.py --once --ssid <ssid>` (asks for the password). Needs a BLE adapter and a badge in pairing mode.
- Executable: `./build-container.sh` on Linux, `./build.sh` on macOS, `build.ps1` on Windows. PyInstaller does not cross-compile. A Linux binary needs a glibc at least as new as the build machine's (the kernel version does not matter), so build Linux binaries with `./build-container.sh`, not a bare `./build.sh`. Check the result with `objdump -T dist/kol-ble-pairing | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1`.
- Releases: set `VERSION` in `kol_ble_pairing.py`, commit, then push the tag `v<VERSION>`; CI fails a tag that does not match `--version`.
- Testers use the executables, so rebuild `dist/` after any change to `kol_ble_pairing.py`.
- There are no automated tests; check encoding changes offline by importing the module and calling `nvs_encode` / `pb_decode`, and pairing changes against a real badge.

## Protocol facts the code depends on

The full reference is `../docs/BLE_PROVISIONING.md` and `../docs/blufi.proto` in the ESP32 workspace; the vendor web app (`https://blufi.gdszzy.com/`, bundle `assets/index-*.js`) is the authority on what the stock firmware expects.

- Only badges named `SM…` with **exactly 10 characters** (e.g. `SMCD510011`) speak this command set (the app's `wifi_new`). Other lengths are the older JSON protocol (`[1,19]` → JSON), and `SMCE…` is 4G, whose vendor bodies are sent **without** the `BD A0` framing. `NAME_LENGTH` enforces this.
- Config is `02 02` (get) and `02 12` (set) with message `BlufiMsg.NVS`, **not** `ble_rpc.proto`'s `ConfigUpdate`, whose field numbers differ (`ble_rpc.proto` is the custom GATT service of SA01A2/SA02A1 badges, with 16-bit opcodes). Replies are `02 <cmd> <status> <protobuf>`.
- Encode like the app: string fields are proto3 `optional` and always sent (empty included); numbers are left out at 0. Limits: 255 bytes per string, 1021 bytes per message.
- Family-1 commands (`[1,12,…]` reset, `[1,30]` set-time) are answered with `01 FF <cmd> <status:24-bit BE>`, not `01 <cmd> <status>`; the tool does not send any today.
- Wi-Fi join uses standard BluFi: SSID `0x02`, password `0x03`, connect `0x03`, status `0x05` → report `0x0f`. Byte 1 of the report: 0 connected, 1 failed, 2 connecting, 3 no IP yet; the app reads 15/202 as wrong password, 201 as network not found, 203 as association failed (and 2 as wrong password too, unconfirmed). Confirmed on fw 3.1.14: a missing SSID is reported as 201 within 5 s, and after that the badge does not answer get-status (`0x05`) at all, so a failure code must end the join at once. "Connected" does not prove the API server is reachable.
- Timezone is a POSIX TZ string with the sign inverted: GMT-3 is sent as `UTC+3`.

## The test badge

The badge on hand is `SMCD510011` (BT `80:B5:4E:5B:16:96`, fw 3.1.14.16). It advertises only while in pairing mode and leaves it after a session, so ask the user to put it back before each run. Running the tool rewrites its server, segment duration and timezone.

## Commits

This is its own git repository. Commit only when the user asks.

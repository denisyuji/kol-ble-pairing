#!/usr/bin/env python3
"""Pair a KOL Smart Badge running the STOCK firmware over BLE.

Sends the Wi-Fi credentials given by the user plus the fixed KOL settings
(API server, segment duration, timezone), makes the badge scan for Wi-Fi
networks, checks the requested network is visible, joins it and waits until
the badge reports it is connected.

Runs on Linux, macOS and Windows (bleak). Protocol: docs/BLE_PROVISIONING.md
(BluFi with DH + AES-128-CFB + CRC16, vendor custom-data command 02 12 for the
config, schema `BlufiMsg.NVS` in docs/blufi.proto).

    python kol_ble_pairing.py --wifi-file line3.wifi      # production line, badge after badge
    python kol_ble_pairing.py --ssid MyNetwork            # asks for the password once
    python kol_ble_pairing.py --ssid MyNetwork --once     # a single badge
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import os
import signal
import struct
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
except ImportError as missing:
    sys.exit(f"missing dependency ({missing.name}): run  pip install -r requirements.txt")
try:  # BluFi needs AES-CFB, which cryptography 47+ moved to "decrepit" (gone from modes in 49)
    from cryptography.hazmat.decrepit.ciphers.modes import CFB
except ImportError:
    from cryptography.hazmat.primitives.ciphers.modes import CFB

# ---------------------------------------------------------------------------
# Fixed pairing parameters
# ---------------------------------------------------------------------------

SERVER = "badgeapi.yourkol.ai"  # config, record and log API
SEGMENT_SECONDS = 600
# GMT-3 (Brasilia time). The badge takes a POSIX/glibc TZ string, whose sign is
# inverted: the stock default "UTC-8" is China time (UTC+08:00), so UTC-03:00
# is written "UTC+3".
TIMEZONE = "UTC+3"
TIMEZONE_LABEL = "GMT-3"

# The stock firmware advertises "SMCD" + serial tail (e.g. SMCD510011).
NAME_PREFIXES = ("SCMD", "SMCD")
# The vendor app speaks this tool's command set (Info `02 01`, NVS `02 02`/`02 12`)
# only to "SM..." names of exactly 10 characters ("wifi_new"); other lengths are
# the older JSON protocol, and "SMCE..." the unframed 4G one.
NAME_LENGTH = 10

SCAN_TIMEOUT = 120  # s waiting for the badge to advertise
AP_SCAN_TIMEOUT = 40  # s for the badge's Wi-Fi scan
CONNECT_TIMEOUT = 90  # s for the badge to join the network

# ---------------------------------------------------------------------------
# BluFi protocol (stock ESP-IDF BluFi, security on)
# ---------------------------------------------------------------------------

BLUFI_WRITE = "0000ff01-0000-1000-8000-00805f9b34fb"
BLUFI_NOTIFY = "0000ff02-0000-1000-8000-00805f9b34fb"

CTRL, DATA = 0, 1
CTRL_SET_SECURITY = 0x01
CTRL_SET_OPMODE = 0x02
CTRL_CONNECT_AP = 0x03
CTRL_GET_STATUS = 0x05
CTRL_GET_AP_LIST = 0x09
DATA_NEG = 0x00
DATA_STA_SSID = 0x02
DATA_STA_PASSWORD = 0x03
DATA_WIFI_REPORT = 0x0F
DATA_AP_LIST = 0x11
DATA_ERROR = 0x12
DATA_CUSTOM = 0x13

FC_ENCRYPTED = 0x01
FC_CHECKSUM = 0x02
FC_FRAGMENT = 0x10
SECURITY_MODE = 0x13  # data encrypted + checksummed, control checksummed

OPMODE_STA = 0x01
STA_CONNECTED, STA_FAILED, STA_CONNECTING, STA_NO_IP = 0, 1, 2, 3
STA_STATES = {0: "connected", 1: "connection failed", 2: "connecting", 3: "connected, waiting for IP"}
# Codes the vendor app reads in the same byte after a failed join (ESP-IDF
# disconnect reasons), with what it tells the user.
STA_FAILURES = {15: "wrong password", 202: "wrong password", 201: "network not found",
                203: "association failed, try again"}

# RFC 2409 group 2 (1024 bits), the group the vendor app negotiates with.
DH_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE65381"
    "FFFFFFFFFFFFFFFF",
    16,
)
DH_GENERATOR = 2

# Vendor commands: [family, method]
VENDOR_GET_CONFIG = bytes([0x02, 0x02])
VENDOR_SET_CONFIG = bytes([0x02, 0x12])

# SetConfig/GetConfig fields (`BlufiMsg.NVS`): number -> (name, is_string)
NVS_FIELDS = {
    1: ("domain_config", True), 2: ("domain_record", True), 3: ("domain_log", True),
    4: ("record_time", False), 5: ("record_ignore_time", False), 6: ("record_mode", False),
    7: ("retry_delay", False), 8: ("timezone", True), 9: ("duor", False),
    10: ("no_switch", False), 11: ("s3_config", True), 12: ("disable_tls", False),
    13: ("compress", True),
}
# The vendor app's limits: longer strings or messages "may become unreadable on
# the device" (its GetConfig reply buffer is 1 KiB).
NVS_MAX_STRING = 255
NVS_MAX_MESSAGE = 1021
# Stock config values (FINDINGS.md), used only if the badge refuses GetConfig.
STOCK_DEFAULTS = {5: 10, 6: 0}
FIXED = {1: SERVER, 2: SERVER, 3: SERVER, 4: SEGMENT_SECONDS, 8: TIMEZONE}


class BadgeError(Exception):
    pass


class Timeout(BadgeError):
    pass


def crc16_genibus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc ^ 0xFFFF


def blufi_checksum(seq: int, data: bytes) -> int:
    return crc16_genibus(bytes([seq, len(data)]) + data)


def aes_cfb(key: bytes, seq: int, data: bytes, decrypt: bool = False) -> bytes:
    cipher = Cipher(algorithms.AES(key), CFB(bytes([seq]) + bytes(15)))
    op = cipher.decryptor() if decrypt else cipher.encryptor()
    return op.update(data) + op.finalize()


def int_to_bytes(value: int) -> bytes:
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


def vendor_wrap(body: bytes) -> bytes:
    return b"\xbd\xa0" + len(body).to_bytes(2, "big") + body + b"\x0a\xdb"


def vendor_unwrap(buffer: bytes) -> tuple[list[bytes], bytes]:
    bodies = []
    while True:
        start = buffer.find(b"\xbd\xa0")
        if start < 0:
            # A trailing 0xbd may be the first half of a split magic.
            return bodies, b"\xbd" if buffer.endswith(b"\xbd") else b""
        buffer = buffer[start:]
        if len(buffer) < 6:
            return bodies, buffer
        length = int.from_bytes(buffer[2:4], "big")
        if len(buffer) < 6 + length:
            return bodies, buffer
        if buffer[4 + length:6 + length] == b"\x0a\xdb":
            bodies.append(buffer[4:4 + length])
        buffer = buffer[6 + length:]


def pb_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def pb_decode(buffer: bytes) -> dict[int, object]:
    """Last value of each field: int for varints, bytes otherwise."""
    fields: dict[int, object] = {}
    i = 0

    def varint() -> int:
        nonlocal i
        value = shift = 0
        while True:
            byte = buffer[i]
            i += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return value

    try:
        while i < len(buffer):
            key = varint()
            number, wire = key >> 3, key & 7
            if wire == 0:
                value = varint()
            elif wire == 2:
                length = varint()
                value = bytes(buffer[i:i + length])
                i += length
            elif wire == 5:
                value, i = buffer[i:i + 4], i + 4
            elif wire == 1:
                value, i = buffer[i:i + 8], i + 8
            else:
                break
            fields[number] = value
    except IndexError:
        pass
    return fields


def nvs_value(fields: dict[int, object], number: int):
    """A field of a decoded NVS message, typed as the schema says (or None)."""
    value = fields.get(number)
    is_string = NVS_FIELDS[number][1]
    if is_string and isinstance(value, bytes):
        return value.decode("utf-8", "replace").rstrip("\0")
    if not is_string and isinstance(value, int):
        return value
    return None


def nvs_encode(values: dict[int, object]) -> bytes:
    """Encodes like the vendor app: the strings are proto3 `optional` and always
    sent (empty included), the numbers have no presence and are left out at 0."""
    message = b""
    for number in sorted(values):
        value = values[number]
        if NVS_FIELDS[number][1]:
            raw = str(value or "").encode()
            if len(raw) > NVS_MAX_STRING:
                raise BadgeError(f"{NVS_FIELDS[number][0]} is longer than {NVS_MAX_STRING} bytes")
            message += pb_varint((number << 3) | 2) + pb_varint(len(raw)) + raw
        elif value:
            message += pb_varint(number << 3) + pb_varint(int(value))
    if len(message) > NVS_MAX_MESSAGE:
        raise BadgeError(f"the configuration is longer than {NVS_MAX_MESSAGE} bytes")
    return message


def parse_wifi_report(data: bytes) -> dict:
    report = {"opmode": data[0], "state": data[1], "ssid": None}
    i = 3
    while i + 2 <= len(data):
        kind, length = data[i], data[i + 1]
        if kind == DATA_STA_SSID:
            report["ssid"] = data[i + 2:i + 2 + length].decode("utf-8", "replace")
        i += 2 + length
    return report


def parse_ap_list(data: bytes) -> list[tuple[str, int]]:
    aps, i = [], 0
    while i + 2 <= len(data):
        length = data[i]  # rssi byte + ssid
        rssi = struct.unpack("b", data[i + 1:i + 2])[0]
        aps.append((data[i + 2:i + 1 + length].decode("utf-8", "replace"), rssi))
        i += 1 + length
    return aps


class BluFi:
    """The phone's side of BluFi: framing, fragmentation, DH, AES, CRC."""

    def __init__(self) -> None:
        self.client: BleakClient | None = None
        self.frame_data = 8
        self.seq = 0
        self.key: bytes | None = None
        self.secure = False
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.fragments = b""
        self.loop = asyncio.get_running_loop()

    def on_disconnect(self, _client) -> None:
        self.loop.call_soon_threadsafe(self.inbox.put_nowait, None)

    async def start(self, client: BleakClient, frame_data: int) -> None:
        self.client, self.frame_data = client, frame_data
        await client.start_notify(BLUFI_NOTIFY, self._on_notify)

    def _on_notify(self, _characteristic, packet: bytearray) -> None:
        packet = bytes(packet)
        if len(packet) < 4:
            return
        type_, fc, seq, length = packet[:4]
        data = packet[4:4 + length]
        if fc & FC_ENCRYPTED:
            if self.key is None:
                return
            data = aes_cfb(self.key, seq, data, decrypt=True)
        if fc & FC_CHECKSUM:
            received = int.from_bytes(packet[4 + length:6 + length], "little")
            if received != blufi_checksum(seq, data):
                return
        if fc & FC_FRAGMENT:
            self.fragments += data[2:]
            return
        data, self.fragments = self.fragments + data, b""
        self.inbox.put_nowait((type_ & 3, type_ >> 2, data))

    def drain(self) -> None:
        while not self.inbox.empty():
            if self.inbox.get_nowait() is None:
                self.inbox.put_nowait(None)
                return

    async def send(self, cls: int, subtype: int, data: bytes = b"", checksum: bool | None = None) -> None:
        encrypt = self.secure and cls == DATA
        checksum = self.secure if checksum is None else checksum
        type_ = (subtype << 2) | cls
        offset = 0
        while True:
            fc = (FC_ENCRYPTED if encrypt else 0) | (FC_CHECKSUM if checksum else 0)
            remaining = len(data) - offset
            if remaining > self.frame_data:
                # First two bytes: bytes left in the whole message, this one included.
                chunk = struct.pack("<H", remaining) + data[offset:offset + self.frame_data - 2]
                offset += self.frame_data - 2
                fc |= FC_FRAGMENT
            else:
                chunk = data[offset:]
            seq, self.seq = self.seq, (self.seq + 1) & 0xFF
            crc = struct.pack("<H", blufi_checksum(seq, chunk)) if fc & FC_CHECKSUM else b""
            payload = aes_cfb(self.key, seq, chunk) if fc & FC_ENCRYPTED else chunk
            await self.client.write_gatt_char(
                BLUFI_WRITE, bytes([type_, fc, seq, len(chunk)]) + payload + crc, response=True)
            if not fc & FC_FRAGMENT:
                return

    async def expect(self, cls: int, subtype: int, timeout: float) -> bytes:
        deadline = self.loop.time() + timeout
        while True:
            remaining = deadline - self.loop.time()
            if remaining <= 0:
                raise Timeout(f"no answer from the badge within {timeout:.0f} s")
            try:
                frame = await asyncio.wait_for(self.inbox.get(), remaining)
            except asyncio.TimeoutError:
                continue
            if frame is None:
                self.inbox.put_nowait(None)
                raise BadgeError("the badge closed the BLE connection")
            got_cls, got_subtype, data = frame
            if (got_cls, got_subtype) == (DATA, DATA_ERROR):
                raise BadgeError(f"the badge reported BluFi error {data.hex() or '?'}")
            if (got_cls, got_subtype) == (cls, subtype):
                return data

    async def negotiate(self) -> None:
        private = int.from_bytes(os.urandom(128), "big") % (DH_PRIME - 2) + 1
        public = pow(DH_GENERATOR, private, DH_PRIME)
        blob = b"".join(len(v).to_bytes(2, "big") + v for v in map(int_to_bytes, (DH_PRIME, DH_GENERATOR, public)))
        await self.send(DATA, DATA_NEG, bytes([0x00]) + len(blob).to_bytes(2, "big"))
        await self.send(DATA, DATA_NEG, bytes([0x01]) + blob)
        badge_public = await self.expect(DATA, DATA_NEG, timeout=15)
        secret = pow(int.from_bytes(badge_public, "big"), private, DH_PRIME)
        self.key = hashlib.md5(int_to_bytes(secret)).digest()
        await self.send(CTRL, CTRL_SET_SECURITY, bytes([SECURITY_MODE]), checksum=True)
        self.secure = True

    async def vendor(self, command: bytes, message: bytes = b"", timeout: float = 15) -> tuple[int, dict]:
        """One vendor command; returns (status, decoded protobuf)."""
        self.drain()
        await self.send(DATA, DATA_CUSTOM, vendor_wrap(command + message))
        deadline = self.loop.time() + timeout
        buffer = b""
        while True:
            buffer += await self.expect(DATA, DATA_CUSTOM, max(deadline - self.loop.time(), 0.1))
            bodies, buffer = vendor_unwrap(buffer)
            for reply in bodies:
                if reply[:2] == command and len(reply) >= 3:
                    return reply[2], pb_decode(reply[3:])


# ---------------------------------------------------------------------------
# Pairing flow
# ---------------------------------------------------------------------------

# "idle" until a BLE link to the badge exists, "paired" until the badge is
# confirmed on Wi-Fi, then "done".
PHASE = "idle"


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


class SeveralBadges(BadgeError):
    pass


async def wait_for_badge(prefixes: tuple[str, ...], name: str | None, skip: set[str],
                         timeout: float | None) -> tuple[BLEDevice, str]:
    """Waits for one badge in pairing mode (timeout None: forever)."""
    found: dict[str, tuple[str, int, BLEDevice]] = {}
    skipped: set[str] = set()
    seen = asyncio.Event()

    def on_advertisement(device, adv) -> None:
        local = adv.local_name or device.name or ""
        if not local.upper().startswith(prefixes) or (name and local.upper() != name.upper()):
            return
        if len(local) != NAME_LENGTH:
            if local not in skipped:
                say(f"ignoring {local}: not a {NAME_LENGTH}-character name, so not a model this tool supports")
                skipped.add(local)
            return
        if local in skip:
            if local not in skipped:
                say(f"ignoring {local}: already paired in this session")
                skipped.add(local)
            return
        if device.address not in found:
            say(f"found badge {local} ({device.address}, {adv.rssi} dBm)")
        found[device.address] = (local, adv.rssi, device)
        seen.set()

    wanted = name or "/".join(p + "..." for p in prefixes)
    say(f"waiting for a badge named {wanted}" + (f" (up to {timeout:.0f} s) ..." if timeout else " ..."))
    scanner = BleakScanner(detection_callback=on_advertisement)
    await scanner.start()
    try:
        try:
            await asyncio.wait_for(seen.wait(), timeout)
        except asyncio.TimeoutError:
            raise BadgeError("no badge found. Is it on, in pairing mode and close to this computer?") from None
        await asyncio.sleep(1)  # give other badges in range a chance to show up
    finally:
        await scanner.stop()

    if len(found) > 1:
        listing = "\n".join(f"    {n}  ({a}, {r} dBm)" for a, (n, r, _) in sorted(found.items(), key=lambda x: -x[1][1]))
        raise SeveralBadges(f"more than one badge in pairing mode:\n{listing}")
    local, _, device = next(iter(found.values()))
    return device, local


async def connect(device: BLEDevice) -> tuple[BleakClient, BluFi]:
    global PHASE
    host = BluFi()
    # A connect that works takes about 4 s; a stuck one never finishes, so retry
    # after 10 s instead of waiting longer.
    for attempt in range(1, 5):
        # The scanner's device object lets bleak connect at once; given only an
        # address it scans for the badge again first. Retries go by address in
        # case the OS has dropped the cached device.
        target = device if attempt == 1 else device.address
        client = BleakClient(target, timeout=10, disconnected_callback=host.on_disconnect)
        try:
            await client.connect()
            break
        except Exception as error:  # noqa: BLE001
            say(f"connection attempt {attempt}/4 failed: {str(error) or type(error).__name__}")
            if attempt == 4:
                raise BadgeError("cannot connect to the badge: is it still in pairing mode?") from None
            await asyncio.sleep(1)
    PHASE = "paired"
    acquire_mtu = getattr(getattr(client, "_backend", None), "_acquire_mtu", None)  # BlueZ only
    if acquire_mtu:
        try:
            await acquire_mtu()
        except Exception:  # noqa: BLE001
            pass
    mtu = client.mtu_size or 23
    # Each BluFi frame (4-byte header + data + 2-byte CRC) must fit one ATT write.
    say(f"BLE connected (MTU {mtu}); negotiating encryption (DH key exchange) ...")
    await host.start(client, max(min(mtu - 3 - 6, 255), 8))
    await host.negotiate()
    say("secure BluFi session established (AES-128 + CRC16)")
    return client, host


def show_value(number: int, value) -> str:
    return repr(value) if NVS_FIELDS[number][1] else str(value)


async def configure(host: BluFi) -> list[str]:
    """Sends server, segment duration and timezone; returns readback mismatches."""
    say("step 2/5: reading the badge's current configuration ...")
    status, current = await host.vendor(VENDOR_GET_CONFIG)
    values = {n: nvs_value(current, n) for n in NVS_FIELDS}
    if status != 0:
        say(f"WARNING: badge refused GetConfig (status {status}); other settings get stock defaults")
        values = {n: STOCK_DEFAULTS.get(n) for n in NVS_FIELDS}

    for number, new in FIXED.items():
        old = values.get(number)
        mark = "" if old == new else f"  (was {show_value(number, old) if old is not None else 'unset'})"
        print(f"    {NVS_FIELDS[number][0]:<15} = {show_value(number, new)}{mark}")
    values.update(FIXED)

    say("step 3/5: sending server, segment duration and timezone ...")
    status, _ = await host.vendor(VENDOR_SET_CONFIG, nvs_encode(values))
    if status != 0:
        raise BadgeError(f"the badge rejected the configuration (status {status})")
    say("the badge accepted the configuration; reading it back to verify ...")

    status, readback = await host.vendor(VENDOR_GET_CONFIG)
    if status != 0:
        say("WARNING: the configuration could not be read back")
        return ["configuration could not be read back"]
    mismatches = [f"{NVS_FIELDS[n][0]} reads back {show_value(n, nvs_value(readback, n))}, expected {show_value(n, v)}"
                  for n, v in FIXED.items() if nvs_value(readback, n) != v]
    for line in mismatches:
        say(f"WARNING: {line}")
    if not mismatches:
        say("configuration verified: the badge stored server, segment duration and timezone")
    return mismatches


async def scan_networks(host: BluFi) -> list[tuple[str, int]]:
    for attempt in range(1, 3):
        say(f"asking the badge to scan for Wi-Fi networks (up to {AP_SCAN_TIMEOUT} s) ...")
        host.drain()
        await host.send(CTRL, CTRL_GET_AP_LIST)
        try:
            aps = parse_ap_list(await host.expect(DATA, DATA_AP_LIST, AP_SCAN_TIMEOUT))
        except Timeout:
            aps = []
        if aps:
            return aps
        say(f"the badge's scan returned nothing (attempt {attempt}/2)")
    return []


def print_networks(aps: list[tuple[str, int]]) -> None:
    best: dict[str, int] = {}
    for ssid, rssi in aps:
        best[ssid] = max(rssi, best.get(ssid, -999))
    print("  Networks seen by the badge:")
    for ssid, rssi in sorted(best.items(), key=lambda item: -item[1]):
        print(f"    {rssi:>4} dBm  {ssid or '<hidden>'}")


async def join_wifi(host: BluFi, ssid: str, password: str) -> None:
    say(f"step 4/5: sending Wi-Fi credentials for {ssid!r} ...")
    host.drain()
    await host.send(CTRL, CTRL_SET_OPMODE, bytes([OPMODE_STA]))
    await host.send(DATA, DATA_STA_SSID, ssid.encode())
    await host.send(DATA, DATA_STA_PASSWORD, password.encode())
    say("Wi-Fi credentials sent (encrypted)")
    say(f"step 5/5: asking the badge to join {ssid!r} (up to {CONNECT_TIMEOUT} s) ...")

    host.drain()
    await host.send(CTRL, CTRL_CONNECT_AP)
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_state = None
    try:
        while (elapsed := loop.time() - started) < CONNECT_TIMEOUT:
            try:
                report = parse_wifi_report(await host.expect(DATA, DATA_WIFI_REPORT, 5))
            except Timeout:
                say(f"no Wi-Fi report yet ({elapsed:.0f} s); asking the badge for its Wi-Fi status ...")
                await host.send(CTRL, CTRL_GET_STATUS)  # ask for a Wi-Fi report
                continue
            state = report["state"]
            if state != last_state:
                say(f"badge Wi-Fi: {STA_STATES.get(state) or STA_FAILURES.get(state, 'state')} ({state})")
                last_state = state
            if state == STA_CONNECTED and report["ssid"] in (None, ssid):
                say(f"the badge reports it is connected to {ssid!r} after {loop.time() - started:.0f} s")
                return
            # A failure reason is the badge's answer to this join (the stock
            # firmware sends it within seconds and then stops reporting), so stop
            # at once. A plain "failed" right after the request may still be the
            # previous state.
            if state in STA_FAILURES:
                raise BadgeError(f"the badge could not join {ssid!r}: {STA_FAILURES[state]} (code {state})")
            if state == STA_FAILED and elapsed > 20:
                raise BadgeError(f"the badge could not join {ssid!r}: check the password")
        hint = " (the vendor app reads code 2 as a wrong password)" if last_state == STA_CONNECTING else ""
        raise BadgeError(f"the badge did not report a connection to {ssid!r} within {CONNECT_TIMEOUT} s{hint}")
    except BadgeError as error:
        # Only a failed join pays for a Wi-Fi scan: it tells a network out of
        # range from a wrong password.
        if not (host.client and host.client.is_connected):
            raise
        say("the join failed; checking which networks the badge can see ...")
        aps = await scan_networks(host)
        visible = [rssi for name, rssi in aps if name == ssid]
        if not visible:
            if aps:
                print_networks(aps)
            raise BadgeError(f"the badge does not see the network {ssid!r}"
                             + ("" if aps else " (its scan found no network at all)")) from None
        say(f"the badge sees {ssid!r} at {max(visible)} dBm, so the network is in range")
        raise error


async def pair(device: BLEDevice, badge: str, ssid: str, password: str) -> list[str]:
    """Pairs one badge; returns config readback mismatches (empty = all good)."""
    global PHASE
    say(f"step 1/5: connecting to {badge} over BLE ...")
    client, host = await connect(device)
    try:
        mismatches = await configure(host)
        await join_wifi(host, ssid, password)
        PHASE = "done"
    finally:
        say(f"disconnecting from {badge} ...")
        try:
            await asyncio.wait_for(client.disconnect(), 10)
        except Exception:  # noqa: BLE001
            pass
    return mismatches


def report_success(badge: str, ssid: str, mismatches: list[str]) -> bool:
    print()
    if mismatches:
        print(f"!!!! {badge}: connected to {ssid!r}, BUT its configuration did not read back as sent:")
        for line in mismatches:
            print(f"       - {line}")
        print("     Verify the badge's settings manually.")
        print()
        return False
    print(f"==== SUCCESS: {badge} is paired and connected to {ssid!r} ====")
    print(f"     server {SERVER} | segment {SEGMENT_SECONDS} s | timezone {TIMEZONE_LABEL} ({TIMEZONE})")
    print()
    return True


def report_failure(badge: str, error: Exception) -> None:
    text = str(error) if isinstance(error, BadgeError) else f"{type(error).__name__}: {error}"
    print()
    print(f"!!!! FAILED: {badge}: {text}")
    if PHASE == "paired":
        print("     The pairing did not finish: verify this badge's current settings manually.")
    print()


# Results of this session: badge name -> "ok" | "check" | "failed"
RESULTS: dict[str, str] = {}
CURRENT = ""


async def session(ssid: str, password: str, name: str | None, once: bool) -> int:
    """Pairs one badge (once) or badge after badge until interrupted."""
    global PHASE, CURRENT
    while True:
        PHASE, CURRENT = "idle", ""
        if not once:
            say("put the NEXT badge in PAIRING MODE (Ctrl-C to finish) ...")
        try:
            device, CURRENT = await wait_for_badge(
                NAME_PREFIXES, name, {b for b, r in RESULTS.items() if r == "ok"},
                SCAN_TIMEOUT if once else None)
        except SeveralBadges as error:
            if once:
                raise BadgeError(f"{error}\n  run again with --name <badge name> to pick one") from None
            print(f"\n{error}\n  leave only one badge in pairing mode; scanning again in 5 s ...\n")
            await asyncio.sleep(5)
            continue

        try:
            mismatches = await pair(device, CURRENT, ssid, password)
        except Exception as error:  # noqa: BLE001  (badge, Bluetooth adapter or OS errors)
            report_failure(CURRENT, error)
            RESULTS[CURRENT] = "failed"
            PHASE = "idle"  # the link is gone: a Ctrl-C now concerns no badge
            if once:
                return 1
            await asyncio.sleep(3)
            continue
        ok = report_success(CURRENT, ssid, mismatches)
        RESULTS[CURRENT] = "ok" if ok else "check"
        if once:
            return 0 if ok else 2


def print_summary() -> None:
    if not RESULTS:
        return
    counts = {r: sum(1 for v in RESULTS.values() if v == r) for r in ("ok", "check", "failed")}
    print(f"Session summary: {counts['ok']} paired, {counts['check']} to verify, {counts['failed']} failed")
    for badge, result in RESULTS.items():
        if result != "ok":
            print(f"  {result:<7}{badge}")


def credentials_file(path: str) -> dict[str, str]:
    """key=value lines (ssid=..., password=...); everything after the first '=' is the value."""
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.rstrip("\r\n")
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip().lower()] = value
    except OSError as error:
        sys.exit(f"error: cannot read {path}: {error.strerror}")
    return values


def read_parameters(args) -> tuple[str, str]:
    """CLI options, then --wifi-file, then environment, then a one-time prompt."""
    stored = credentials_file(args.wifi_file) if args.wifi_file else {}
    ssid = args.ssid or stored.get("ssid") or os.environ.get("KOL_WIFI_SSID") or input("Wi-Fi SSID: ")
    password = (args.password or stored.get("password") or os.environ.get("KOL_WIFI_PASSWORD")
                or getpass.getpass(f"Wi-Fi password for {ssid!r}: "))
    if not ssid:
        sys.exit("error: the Wi-Fi SSID is required")
    if not password:
        sys.exit("error: the Wi-Fi password is required")
    if len(ssid.encode()) > 32:
        sys.exit("error: the SSID is longer than 32 bytes")
    if len(password.encode()) > 64:
        sys.exit("error: the password is longer than 64 bytes")
    return ssid, password


def raise_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wifi-file", metavar="FILE", help="file with ssid=... and password=... lines")
    parser.add_argument("--ssid", help="Wi-Fi network name (default: --wifi-file, $KOL_WIFI_SSID, or asked once)")
    parser.add_argument("--password", help="Wi-Fi password (default: --wifi-file, $KOL_WIFI_PASSWORD, or asked once)")
    parser.add_argument("--name", help="pair only the badge with this BLE name, e.g. SMCD510011")
    parser.add_argument("--once", action="store_true", help="pair a single badge and exit")
    args = parser.parse_args()

    # Closing the app other than with Ctrl-C is handled the same way.
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), raise_interrupt)

    try:
        ssid, password = read_parameters(args)
        print()
        print(f"  Wi-Fi network : {ssid}")
        print(f"  API server    : {SERVER} (config, record, log)")
        print(f"  Segment       : {SEGMENT_SECONDS} s")
        print(f"  Timezone      : {TIMEZONE_LABEL}")
        print(f"  Mode          : {'single badge' if args.once else 'production line (badge after badge, Ctrl-C to finish)'}")
        print()
        if args.once:
            input("Put the badge in PAIRING MODE, then press Enter to start (Ctrl-C to quit) ...")
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Nothing was sent to any badge.")
        return 130

    try:
        return asyncio.run(session(ssid, password, args.name, args.once))
    except KeyboardInterrupt:
        print()
        if PHASE == "paired":
            print(f"Interrupted (Ctrl-C) while connected to {CURRENT}: its pairing did NOT finish.")
            print("Verify that badge's current settings manually (Wi-Fi, server, segment duration, timezone).")
            RESULTS[CURRENT] = "check"
        elif PHASE == "done":
            print(f"Interrupted while disconnecting; {CURRENT} had already reported it is on Wi-Fi.")
            RESULTS.setdefault(CURRENT, "ok")
        else:
            print("Interrupted while no badge was connected.")
        print_summary()
        return 130
    except BadgeError as error:  # --once: no badge found / several badges
        print(f"\nERROR: {error}")
        return 1
    except Exception as error:  # noqa: BLE001  (Bluetooth adapter / OS errors while scanning)
        print(f"\nERROR: {type(error).__name__}: {error}")
        print_summary()
        return 1


if __name__ == "__main__":
    sys.exit(main())

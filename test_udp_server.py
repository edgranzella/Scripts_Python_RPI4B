#!/usr/bin/env python3
"""
Test de tramas UDP para BKP-A7670SA Gateway
Envía las 10 tramas CID al servidor y muestra la respuesta ACK.

Uso:
    python3 test_udp_server.py [IP_SERVIDOR] [PUERTO]

Por defecto: 190.111.217.188:57777
"""

import socket
import sys
import time

# ── Configuración ─────────────────────────────────────────────────────────────
SERVER_IP   = sys.argv[1] if len(sys.argv) > 1 else "190.111.217.188"
SERVER_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 57777
SERIAL      = 12345678
TIMEOUT_S   = 5.0

# ── Algoritmos del firmware ───────────────────────────────────────────────────

def pack_bcd(high: str, low: str) -> int:
    return ((ord(high) - ord('0')) << 4) | (ord(low) - ord('0'))

def xor_checksum(data: bytearray, start: int, end: int) -> int:
    xorv = 0
    for i in range(start, end + 1):
        xorv ^= data[i]
    return xorv

def cid_checksum1(buf: bytearray) -> int:
    checksum = 0
    for i in range(6, 21):
        b = buf[i]
        if b == 0 and i < 12:
            b = 0x0A
        checksum += b
    while checksum > 15:
        checksum -= 15
    checksum += 15
    return checksum & 0xFF

def fill_serial(out: bytearray, serial: int):
    s = f"{serial:08d}"
    out[2] = pack_bcd(s[7], s[6])
    out[3] = pack_bcd(s[5], s[4])
    out[4] = pack_bcd(s[3], s[2])
    out[5] = pack_bcd(s[1], s[0])

def rtc_fill(out: bytearray, pos: int):
    # Usa hora real del sistema
    import datetime
    now = datetime.datetime.now()
    hh, mm, ss = now.hour, now.minute, now.second
    dd, mo, yr = now.day, now.month, now.year
    out[pos+0]  = ord('0') + hh   // 10
    out[pos+1]  = ord('0') + hh   %  10
    out[pos+2]  = ord('0') + mm   // 10
    out[pos+3]  = ord('0') + mm   %  10
    out[pos+4]  = ord('0') + ss   // 10
    out[pos+5]  = ord('0') + ss   %  10
    out[pos+6]  = ord('0') + dd   // 10
    out[pos+7]  = ord('0') + dd   %  10
    out[pos+8]  = ord('0') + mo   // 10
    out[pos+9]  = ord('0') + mo   %  10
    out[pos+10] = ord('0') + (yr  // 1000) % 10
    out[pos+11] = ord('0') + (yr  // 100)  % 10
    out[pos+12] = ord('0') + (yr  // 10)   % 10
    out[pos+13] = ord('0') +  yr            % 10

def parse_cid(line: str) -> dict:
    return dict(
        account    = line[7:11],
        qualifier  = line[11],
        event_code = line[12:15],
        partition  = line[15:17],
        zone       = line[17:20]
    )

def build_event(cid_line: str, serial: int, seq: int) -> bytearray:
    msg = parse_cid(cid_line)
    out = bytearray(43)
    out[0] = 0x40
    out[1] = 0xE8
    fill_serial(out, serial)
    out[6]  = ord(msg['account'][0])
    out[7]  = ord(msg['account'][1])
    out[8]  = ord(msg['account'][2])
    out[9]  = ord(msg['account'][3])
    out[10] = 0x01
    out[11] = 0x08
    out[12] = 0x01 if msg['qualifier'] == 'E' else (0x03 if msg['qualifier'] == 'R' else 0x00)
    out[13] = ord(msg['event_code'][0])
    out[14] = ord(msg['event_code'][1])
    out[15] = ord(msg['event_code'][2])
    out[16] = ord(msg['partition'][0])
    out[17] = ord(msg['partition'][1])
    out[18] = ord(msg['zone'][0])
    out[19] = ord(msg['zone'][1])
    out[20] = ord(msg['zone'][2])
    out[21] = cid_checksum1(out)
    out[22] = seq & 0xFF
    out[23] = 0x00
    out[24] = 0x51
    rtc_fill(out, 28)
    out[42] = xor_checksum(out, 1, 41)
    return out

def is_ack(data: bytes) -> bool:
    return len(data) >= 2 and data[0] == 0x40 and (data[1] & 0xF0) == 0x30

# ── Tramas de prueba ──────────────────────────────────────────────────────────

CID_FRAMES = [
    "5051 182800R35600000",
    "5051 182800E38558000",
    "5051 182800E53058000",
    "5051 182800E53058000",
    "5051 182800E53011001",
    "5051 182800E53021001",
    "5051 182800E99000000",
    "5051 182800E35600002",
    "5051 182800R38558000",
    "5051 182800R38658000",
]

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\nDestino: {SERVER_IP}:{SERVER_PORT}")
    print(f"Serial:  {SERIAL}")
    print("=" * 65)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT_S)

    ok_count  = 0
    err_count = 0

    for seq, cid in enumerate(CID_FRAMES, start=1):
        frame = build_event(cid, SERIAL, seq)
        msg   = parse_cid(cid)
        hex_s = ' '.join(f'{b:02X}' for b in frame)

        print(f"\n[{seq:02d}] CID: {cid}")
        print(f"     Q={msg['qualifier']} event={msg['event_code']} "
              f"part={msg['partition']} zone={msg['zone']}")
        print(f"     TX ({len(frame)}B): {hex_s}")

        try:
            sock.sendto(bytes(frame), (SERVER_IP, SERVER_PORT))
            resp, addr = sock.recvfrom(64)
            hex_r = ' '.join(f'{b:02X}' for b in resp)
            if is_ack(resp):
                print(f"     RX ACK ✓: {hex_r}  (de {addr[0]}:{addr[1]})")
                ok_count += 1
            else:
                print(f"     RX datos (no ACK): {hex_r}")
                err_count += 1
        except socket.timeout:
            print(f"     TIMEOUT — servidor no respondió en {TIMEOUT_S}s")
            err_count += 1

        time.sleep(0.3)

    sock.close()
    print(f"\n{'=' * 65}")
    print(f"Resultado: {ok_count} ACK recibidos, {err_count} sin respuesta")
    print("=" * 65)

if __name__ == "__main__":
    main()

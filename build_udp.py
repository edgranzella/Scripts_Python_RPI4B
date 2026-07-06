#!/usr/bin/env python3
"""
Simulación exacta de UDP_Build_Event() y UDP_Build_Heartbeat()
del firmware STM32 para el gateway BKP-A7670SA.
Serial number por defecto: 12345678
Timestamp fijo para reproducibilidad: 2026-07-02 10:30:00
"""

# ── Utilidades ────────────────────────────────────────────────────────────────

def pack_bcd(high: str, low: str) -> int:
    """Conversión ASCII → nibble BCD. Igual que pack_bcd() en C."""
    return ((ord(high) - ord('0')) << 4) | (ord(low) - ord('0'))

def xor_checksum(data: bytearray, start: int, end: int) -> int:
    """XOR_Checksum(data, start, end) — índices inclusivos."""
    xorv = 0
    for i in range(start, end + 1):
        xorv ^= data[i]
    return xorv

def cid_checksum1(buf: bytearray) -> int:
    """CID_Checksum1: suma módulo 15 de bytes[6..20], +15 al final."""
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

def rtc_fill_ascii_datetime(dst: bytearray, pos: int,
                             hh=10, mm=30, ss=0,
                             dd=2, month=7, year=2026):
    """
    RTC_Fill_ASCII_DateTime: rellena 14 bytes ASCII en out[28..41]
    Formato: HH MM SS DD MM YYYY
    """
    dst[pos+0]  = ord('0') + hh   // 10
    dst[pos+1]  = ord('0') + hh   %  10
    dst[pos+2]  = ord('0') + mm   // 10
    dst[pos+3]  = ord('0') + mm   %  10
    dst[pos+4]  = ord('0') + ss   // 10
    dst[pos+5]  = ord('0') + ss   %  10
    dst[pos+6]  = ord('0') + dd   // 10
    dst[pos+7]  = ord('0') + dd   %  10
    dst[pos+8]  = ord('0') + month // 10
    dst[pos+9]  = ord('0') + month %  10
    dst[pos+10] = ord('0') + (year // 1000) % 10
    dst[pos+11] = ord('0') + (year // 100)  % 10
    dst[pos+12] = ord('0') + (year // 10)   % 10
    dst[pos+13] = ord('0') +  year           % 10

def fill_serial(out: bytearray, serial: int):
    """Rellena out[2..5] con el número de serie en BCD inverso."""
    s = f"{serial:08d}"
    # pack_bcd(serial_str[7], serial_str[6]) → out[2], etc.
    out[2] = pack_bcd(s[7], s[6])
    out[3] = pack_bcd(s[5], s[4])
    out[4] = pack_bcd(s[3], s[2])
    out[5] = pack_bcd(s[1], s[0])

# ── Parseo de trama CID ───────────────────────────────────────────────────────

def parse_cid(line: str) -> dict:
    """
    CID_Parse_Event: extrae campos de la trama ContactID.
    Formato: 'SSSS HHMMSSAAAA Q EEE PP ZZZ'
    Posiciones según el firmware (base 0 sobre el string raw):
      rx[7..10]  → account (4 chars)
      rx[11]     → qualifier (E/R)
      rx[12..14] → event_code (3 chars)
      rx[15..16] → partition (2 chars)
      rx[17..19] → zone (3 chars)
    """
    # La trama real tiene un espacio en pos 4: '5051 182800E35600000'
    # rx[7] = '2', rx[8]='8', rx[9]='0', rx[10]='0' → account='2800' (WRONG)
    # Verificar: '5051 182800E35600000'
    #  pos: 0123456789012345678901
    #             ^      ^
    #  rx[7]='2' rx[11]='E' rx[12]='3' rx[15]='0' rx[17]='0'
    account    = line[7:11]
    qualifier  = line[11]
    event_code = line[12:15]
    partition  = line[15:17]
    zone       = line[17:20]
    return dict(account=account, qualifier=qualifier,
                event_code=event_code, partition=partition, zone=zone)

# ── Construccores de tramas ───────────────────────────────────────────────────

def udp_build_event(cid_line: str, serial: int = 12345678, seq: int = 1) -> bytearray:
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

    if msg['qualifier'] == 'E':
        out[12] = 0x01
    elif msg['qualifier'] == 'R':
        out[12] = 0x03
    else:
        out[12] = 0x00

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

    # out[25..27] quedan en 0x00 (memset)
    rtc_fill_ascii_datetime(out, 28)

    out[42] = xor_checksum(out, 1, 41)

    return out

# ── Main ──────────────────────────────────────────────────────────────────────

cid_frames = [
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

SERIAL = 12345678

print("=" * 70)
print("TRAMAS UDP listas para pegar en minicom luego del prompt '>'")
print(f"Serial number: {SERIAL}  |  Timestamp fijo: 2026-07-02 10:30:00")
print("=" * 70)

for seq, cid in enumerate(cid_frames, start=1):
    msg = parse_cid(cid)
    frame = udp_build_event(cid, serial=SERIAL, seq=seq)

    hex_str   = ' '.join(f'{b:02X}' for b in frame)
    raw_bytes = bytes(frame)

    print(f"\n{'─'*70}")
    print(f"CID:       {cid}")
    print(f"  account={msg['account']}  qualifier={msg['qualifier']}  "
          f"event={msg['event_code']}  part={msg['partition']}  zone={msg['zone']}")
    print(f"  seq={seq}  len={len(frame)} bytes")
    print(f"HEX:       {hex_str}")
    print(f"Checksum1: 0x{frame[21]:02X}  XOR final: 0x{frame[42]:02X}")

    # Mostrar como secuencia escapada para minicom/picocom
    escaped = ''
    for b in raw_bytes:
        if 0x20 <= b <= 0x7E and b != ord('\\'):
            escaped += chr(b)
        else:
            escaped += f'\\x{b:02x}'

    print(f"MINICOM:   {escaped}")

print(f"\n{'=' * 70}")
print("NOTA: los bytes no imprimibles aparecen como \\xNN.")
print("En minicom: Ctrl+A → W para enviar secuencias hex, o usá el")
print("script de Python para enviar directamente por UDP.")
print("=" * 70)


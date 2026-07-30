#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rs485_bench.py — Banco de pruebas standalone para el firmware RS-485 (Arduino + MAX485).

Deriva del protocolo descripto en:
  - Documento 1 (Manual técnico RS-485)
  - Documento 2 (Especificación de software)

Funcionalidad:
  * Conexión serial 9600 8N1 (configurable), con opción de toggle RTS (DE/RE manual).
  * CRC XOR de 8 bits (: .. antes de '*'), construcción y validación de tramas.
  * Hilo de recepción continuo -> nunca se pierde un evento FIFO asíncrono.
  * Clasificación y decodificación de R / RS / A / CAL / BT,RES / eventos EV.
  * Auto-ACK de eventos FIFO con deduplicación por ID.
  * CLI interactiva con aliases de comandos.
  * Modos: interactivo, polling automático, sniffer puro, selftest (prueba mínima §13).
  * Inyección de errores (CRC malo, comando desconocido, param inválido, dirección ajena).
  * Logging con timestamp del host + colores + volcado a archivo JSONL.

Uso rápido:
  python rs485_bench.py --port /dev/ttyUSB0
  python rs485_bench.py --port COM3 --poll 2 --auto-ack
  python rs485_bench.py --port /dev/ttyUSB0 --monitor          # solo escuchar
  python rs485_bench.py --port /dev/ttyUSB0 --selftest

Dependencias:
  pip install pyserial
  pip install colorama   (opcional; si falta, se corre sin color)
"""

import argparse
import json
import queue
import re
import sys
import threading
import time
from datetime import datetime

try:
    import serial  # pyserial
except ImportError:
    print("ERROR: falta pyserial. Instalá con:  pip install pyserial", file=sys.stderr)
    sys.exit(1)

# ---- Color opcional -------------------------------------------------------
try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init()
    _HAS_COLOR = True
except ImportError:
    _HAS_COLOR = False

    class _Dummy:
        def __getattr__(self, _):
            return ""
    Fore = Style = _Dummy()  # type: ignore


# ===========================================================================
#  CAPA DE PROTOCOLO (sin hardware -> testeable con unit tests)
# ===========================================================================

ADDR = "01"  # dirección fija del equipo

def crc_xor(cuerpo: str) -> int:
    """XOR de 8 bits sobre todos los chars desde ':' hasta el último antes de '*'."""
    c = 0
    for ch in cuerpo:
        c ^= ord(ch)
    return c & 0xFF


def build_frame(payload: str, with_crc: bool = True, bad_crc: bool = False,
                eol: str = "\r\n") -> str:
    """
    Arma una trama a partir del cuerpo (ej. ':01,S').
    with_crc -> agrega '*HH'. bad_crc -> agrega un CRC deliberadamente incorrecto.
    """
    if not payload.startswith(":"):
        payload = ":" + payload
    if with_crc:
        hh = crc_xor(payload)
        if bad_crc:
            hh = (hh ^ 0xFF) & 0xFF  # rompemos el CRC a propósito
        payload = f"{payload}*{hh:02X}"
    return payload + eol


def parse_frame(raw: str) -> dict:
    """
    Parsea una trama entrante (sin EOL). Devuelve un dict con:
      raw, ok_addr, has_crc, crc_ok, crc_rx, crc_calc, body, fields, kind, id, decoded
    kind ∈ {'R','RS','A','CAL','BT_RES','EVENT','UNKNOWN'}
    """
    out = {
        "raw": raw, "ok_addr": False, "has_crc": False, "crc_ok": None,
        "crc_rx": None, "crc_calc": None, "body": None, "fields": [],
        "kind": "UNKNOWN", "id": None, "decoded": None,
    }
    s = raw.strip("\r\n")
    if not s.startswith(f":{ADDR}"):
        return out
    out["ok_addr"] = True

    # separar CRC
    body = s
    if "*" in s:
        out["has_crc"] = True
        body, hh = s.rsplit("*", 1)
        out["body"] = body
        try:
            out["crc_rx"] = int(hh, 16)
            out["crc_calc"] = crc_xor(body)
            out["crc_ok"] = (out["crc_rx"] == out["crc_calc"])
        except ValueError:
            out["crc_ok"] = False
    else:
        out["body"] = body

    fields = body.split(",")
    out["fields"] = fields
    # fields[0] = ':01', fields[1] = tipo/ID
    if len(fields) < 2:
        return out
    f1 = fields[1]

    # ¿Evento FIFO? -> el 2º campo es un ID hex de 4 dígitos y el 3º es 'EV'
    if len(fields) >= 3 and fields[2] == "EV" and re.fullmatch(r"[0-9A-Fa-f]{1,4}", f1):
        out["kind"] = "EVENT"
        out["id"] = f1.upper()
        out["decoded"] = _decode_event(fields)
        return out

    if f1 == "R":
        out["kind"] = "R"
        out["decoded"] = _decode_R(fields)
    elif f1 == "RS":
        out["kind"] = "RS"
        out["decoded"] = _decode_RS(fields)
    elif f1 == "A":
        out["kind"] = "A"
        out["decoded"] = ",".join(fields[2:]) if len(fields) > 2 else ""
    elif f1 == "CAL":
        out["kind"] = "CAL"
        out["decoded"] = _decode_CAL(fields)
    elif f1 == "BT":
        out["kind"] = "BT_RES"
        out["decoded"] = ",".join(fields[2:])
    return out


def _decode_R(f):
    # :01,R,<AC>,<F1_ON>,<F2_ON>,<VB_AD>,<IB_AD>,<IC_AD>,<PWM>,<FL>,<TS>
    keys = ["AC", "F1_ON", "F2_ON", "VB_AD", "IB_AD", "IC_AD", "PWM", "FL"]
    vals = f[2:]
    d = {}
    for k, v in zip(keys, vals):
        d[k] = v
    if len(vals) > len(keys):
        d["TS"] = ",".join(vals[len(keys):])  # el TS puede traer coma? no, pero por si acaso
    _annotate_fl(d)
    return d


def _decode_RS(f):
    keys = ["AC", "F1_ON", "F2_ON", "VB_AD", "IB_AD", "IC_AD", "PWM", "FL",
            "TST_ACT", "TST_MODE", "TST_ELAPSED_S", "LAST_MM", "LAST_SS"]
    vals = f[2:]
    d = {}
    for k, v in zip(keys, vals):
        d[k] = v
    if len(vals) > len(keys):
        d["TS"] = ",".join(vals[len(keys):])
    _annotate_fl(d)
    mode_map = {"0": "ninguno", "1": "USB", "2": "mensual", "3": "485"}
    if "TST_MODE" in d:
        d["TST_MODE_txt"] = mode_map.get(d["TST_MODE"], "?")
    return d


def _annotate_fl(d):
    if "FL" in d:
        try:
            fl = int(d["FL"])
            d["FL_F1_fail"] = bool(fl & 0x01)
            d["FL_F2_fail"] = bool(fl & 0x02)
        except ValueError:
            pass


def _decode_CAL(f):
    keys = ["ad_f_min", "ad_f_max", "ad_vb_min", "ad_vb_test", "ad_vb_max", "ibat_500mA_ad"]
    vals = f[2:]
    return {k: v for k, v in zip(keys, vals)}


def _decode_event(f):
    # :01,<id>,EV,<campo>,<val>,... ,TS,<...>
    return ",".join(f[3:])


# ===========================================================================
#  CAPA DE TRANSPORTE + LOGGING
# ===========================================================================

class Bench:
    def __init__(self, port, baud=9600, timeout=0.1, rts_toggle=False,
                 with_crc=True, auto_ack=True, eol="\r\n", logfile=None,
                 no_color=False):
        self.port_name = port
        self.with_crc = with_crc
        self.auto_ack = auto_ack
        self.eol = eol
        self.rts_toggle = rts_toggle
        self.logfile = open(logfile, "a", encoding="utf-8") if logfile else None
        self.use_color = _HAS_COLOR and not no_color

        self.ser = serial.Serial(
            port=port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
            timeout=timeout,
        )
        if self.rts_toggle:
            self.ser.setRTS(False)  # RX por defecto

        self.rx_queue = queue.Queue()
        self._stop = threading.Event()
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)

        # dedup de eventos FIFO
        self.seen_ids = set()

        # contadores de sesión
        self.stats = {"tx": 0, "rx": 0, "crc_ok": 0, "crc_bad": 0,
                      "events": 0, "acks": 0, "dups": 0}

    # ---- ciclo de vida ----
    def start(self):
        self._rx_thread.start()

    def close(self):
        self._stop.set()
        time.sleep(0.2)
        try:
            self.ser.close()
        except Exception:
            pass
        if self.logfile:
            self.logfile.close()

    # ---- envío ----
    def send_body(self, body, with_crc=None, bad_crc=False):
        wc = self.with_crc if with_crc is None else with_crc
        frame = build_frame(body, with_crc=wc, bad_crc=bad_crc, eol=self.eol)
        self._tx_raw(frame)

    def send_raw(self, text):
        if not text.endswith(("\n", "\r")):
            text += self.eol
        self._tx_raw(text)

    def _tx_raw(self, frame):
        if self.rts_toggle:
            self.ser.setRTS(True)   # DE=1 -> TX
            time.sleep(0.001)
        self.ser.write(frame.encode("ascii", errors="replace"))
        self.ser.flush()
        if self.rts_toggle:
            time.sleep(0.001)
            self.ser.setRTS(False)  # volver a RX
        self.stats["tx"] += 1
        self._log("TX", frame.rstrip("\r\n"), None)

    # ---- recepción (hilo) ----
    def _rx_loop(self):
        buf = bytearray()
        while not self._stop.is_set():
            try:
                data = self.ser.read(256)
            except Exception:
                break
            if not data:
                continue
            for b in data:
                if b == 0x0D:      # \r ignorar
                    continue
                if b == 0x0A:      # \n fin de trama
                    line = buf.decode("ascii", errors="replace")
                    buf.clear()
                    if line:
                        self._handle_line(line)
                else:
                    buf.append(b)

    def _handle_line(self, line):
        self.stats["rx"] += 1
        parsed = parse_frame(line)
        if parsed["has_crc"]:
            if parsed["crc_ok"]:
                self.stats["crc_ok"] += 1
            else:
                self.stats["crc_bad"] += 1
        self._log("RX", line, parsed)
        self.rx_queue.put(parsed)

        # Auto-ACK de eventos FIFO
        if parsed["kind"] == "EVENT":
            self.stats["events"] += 1
            eid = parsed["id"]
            # Solo ACKeamos si el CRC es válido (o no vino CRC)
            crc_ok = parsed["crc_ok"] in (True, None)
            if not crc_ok:
                self._note(f"Evento {eid} con CRC inválido -> NO se ACKea (descartado)")
                return
            dup = eid in self.seen_ids
            if dup:
                self.stats["dups"] += 1
            else:
                self.seen_ids.add(eid)
            if self.auto_ack:
                self.send_body(f":{ADDR},ACK,{eid}")
                self.stats["acks"] += 1
                tag = "DUP (re-ACK, no reprocesado)" if dup else "ACK enviado"
                self._note(f"Evento {eid}: {tag}")

    # ---- logging / display ----
    def _log(self, direction, raw, parsed):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        arrow = "->" if direction == "TX" else "<-"
        color = ""
        decoded_line = ""

        if parsed is not None:
            kind = parsed["kind"]
            if parsed["has_crc"] and parsed["crc_ok"] is False:
                color = Fore.RED
                decoded_line = (f"   CRC MISMATCH rx={parsed['crc_rx']:02X} "
                                f"calc={parsed['crc_calc']:02X}") if parsed["crc_rx"] is not None else "   CRC inválido"
            elif kind == "EVENT":
                color = Fore.YELLOW
                decoded_line = f"   EVENTO id={parsed['id']} -> {parsed['decoded']}"
            elif kind in ("R", "RS"):
                color = Fore.CYAN
                decoded_line = "   " + _fmt_dict(parsed["decoded"])
            elif kind == "A":
                txt = parsed["decoded"] or ""
                color = Fore.RED if ("ERR" in txt) else Fore.GREEN
                decoded_line = f"   A -> {txt}"
            elif kind == "CAL":
                color = Fore.CYAN
                decoded_line = "   CAL " + _fmt_dict(parsed["decoded"])
            elif kind == "BT_RES":
                color = Fore.CYAN
                decoded_line = f"   BT,RES -> {parsed['decoded']}"
            else:
                color = Fore.WHITE
        else:
            color = Style.DIM if _HAS_COLOR else ""

        if self.use_color:
            print(f"{Style.DIM}{ts}{Style.RESET_ALL} {arrow} {color}{raw}{Style.RESET_ALL}")
            if decoded_line:
                print(f"{color}{decoded_line}{Style.RESET_ALL}")
        else:
            print(f"{ts} {arrow} {raw}")
            if decoded_line:
                print(decoded_line)

        if self.logfile:
            rec = {
                "host_ts": datetime.now().isoformat(),
                "dir": direction, "raw": raw,
                "kind": parsed["kind"] if parsed else None,
                "crc_ok": parsed["crc_ok"] if parsed else None,
                "decoded": parsed["decoded"] if parsed else None,
            }
            self.logfile.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.logfile.flush()

    def _note(self, msg):
        if self.use_color:
            print(f"{Fore.MAGENTA}   [i] {msg}{Style.RESET_ALL}")
        else:
            print(f"   [i] {msg}")

    def print_stats(self):
        print("\n--- Estadísticas de sesión ---")
        for k, v in self.stats.items():
            print(f"  {k:10s}: {v}")
        print(f"  ids vistos: {len(self.seen_ids)}")


def _fmt_dict(d):
    if not isinstance(d, dict):
        return str(d)
    return " ".join(f"{k}={v}" for k, v in d.items())


# ===========================================================================
#  COMANDOS DE LA CLI
# ===========================================================================

HELP_TEXT = """
Comandos disponibles:
  poll | s            -> :01,S           (estado inmediato)
  st                  -> :01,ST          (estado extendido)
  td now              -> setea hora del host
  td Y M D h m s      -> :01,TD,Y,M,D,h,m,s
  cal show            -> :01,CAL,SHOW
  cal vf <n>          -> :01,CAL,VF,<n>
  cal vb <n>          -> :01,CAL,VB,<n>
  cal ib <n>          -> :01,CAL,IB,<n>
  rst                 -> :01,C,RST
  bt start            -> :01,BT,START
  bt res              -> :01,BT,RES?
  ack <id>            -> :01,ACK,<id>     (ACK manual)
  raw <texto>         -> envía exactamente <texto>
  crc on|off          -> activa/desactiva el *HH en los envíos
  poll on <seg> | poll off   -> polling automático de :01,S

Inyección de errores (para probar robustez del firmware):
  err crc             -> envía :01,S con CRC deliberadamente malo (espera CRCERR)
  err cmd             -> envía comando desconocido (espera ERR)
  err param           -> envía TD con fecha fuera de rango (espera ERR)
  err addr            -> envía :02,S (dirección ajena; el equipo debe ignorar)

Utilidades:
  stats               -> muestra contadores de sesión
  help                -> esta ayuda
  quit | exit         -> salir
"""


def handle_command(bench: Bench, line: str, poll_ctl: dict):
    parts = line.strip().split()
    if not parts:
        return True
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("quit", "exit"):
        return False
    elif cmd == "help":
        print(HELP_TEXT)
    elif cmd == "stats":
        bench.print_stats()
    elif cmd in ("poll", "s") and not args:
        bench.send_body(f":{ADDR},S")
    elif cmd == "st":
        bench.send_body(f":{ADDR},ST")
    elif cmd == "td":
        if args and args[0].lower() == "now":
            n = datetime.now()
            bench.send_body(f":{ADDR},TD,{n.year},{n.month:02d},{n.day:02d},"
                            f"{n.hour:02d},{n.minute:02d},{n.second:02d}")
        elif len(args) == 6:
            bench.send_body(f":{ADDR},TD," + ",".join(args))
        else:
            print("Uso: td now  |  td YYYY MM DD hh mm ss")
    elif cmd == "cal":
        if args and args[0].lower() == "show":
            bench.send_body(f":{ADDR},CAL,SHOW")
        elif len(args) == 2 and args[0].lower() in ("vf", "vb", "ib"):
            bench.send_body(f":{ADDR},CAL,{args[0].upper()},{args[1]}")
        else:
            print("Uso: cal show | cal vf <n> | cal vb <n> | cal ib <n>")
    elif cmd == "rst":
        bench.send_body(f":{ADDR},C,RST")
    elif cmd == "bt":
        if args and args[0].lower() == "start":
            bench.send_body(f":{ADDR},BT,START")
        elif args and args[0].lower() == "res":
            bench.send_body(f":{ADDR},BT,RES?")
        else:
            print("Uso: bt start | bt res")
    elif cmd == "ack" and len(args) == 1:
        bench.send_body(f":{ADDR},ACK,{args[0].upper()}")
    elif cmd == "raw":
        bench.send_raw(line[4:].strip())
    elif cmd == "crc" and args:
        bench.with_crc = (args[0].lower() == "on")
        print(f"CRC en envíos: {'ON' if bench.with_crc else 'OFF'}")
    elif cmd == "poll" and args:
        if args[0].lower() == "on":
            interval = float(args[1]) if len(args) > 1 else 2.0
            poll_ctl["interval"] = interval
            poll_ctl["on"] = True
            print(f"Polling automático ON cada {interval}s")
        elif args[0].lower() == "off":
            poll_ctl["on"] = False
            print("Polling automático OFF")
    elif cmd == "err" and args:
        sub = args[0].lower()
        if sub == "crc":
            bench.send_body(f":{ADDR},S", with_crc=True, bad_crc=True)
        elif sub == "cmd":
            bench.send_body(f":{ADDR},ZZZ")  # comando inexistente
        elif sub == "param":
            bench.send_body(f":{ADDR},TD,2026,13,45,99,99,99")  # fecha inválida
        elif sub == "addr":
            other = ":02,S"
            if bench.with_crc:
                other = f"{other}*{crc_xor(other):02X}"
            bench.send_raw(other)
        else:
            print("Uso: err crc | err cmd | err param | err addr")
    else:
        print(f"Comando no reconocido: '{line}'. Tipeá 'help'.")
    return True


# ===========================================================================
#  MODOS ESPECIALES
# ===========================================================================

def run_selftest(bench: Bench):
    """Prueba mínima recomendada (§13 del manual)."""
    print("\n=== SELFTEST (prueba mínima §13) ===")

    def wait_for(pred, timeout, label):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                p = bench.rx_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if pred(p):
                print(f"  [PASS] {label}")
                return p
        print(f"  [FAIL] {label} (timeout {timeout}s)")
        return None

    # 1) setear hora
    print("1) Seteo de hora...")
    bench.send_body(f":{ADDR},TD," + datetime.now().strftime("%Y,%m,%d,%H,%M,%S"))
    wait_for(lambda p: p["kind"] == "A", 2, "TD -> respuesta A")

    # 2) poll
    print("2) Poll de estado...")
    _drain(bench.rx_queue)
    bench.send_body(f":{ADDR},S")
    wait_for(lambda p: p["kind"] == "R", 2, "S -> R")

    # 3) esperar evento AC (simulación manual de caída de red)
    print("3) Esperando EV,AC,OFF (simulá la caída de 220 V ahora)...")
    wait_for(lambda p: p["kind"] == "EVENT" and "AC,OFF" in (p["decoded"] or ""),
             15, "EV,AC,OFF (+ auto-ACK)")

    # 4) test de batería
    print("4) Iniciando test de batería...")
    _drain(bench.rx_queue)
    bench.send_body(f":{ADDR},BT,START")
    wait_for(lambda p: p["kind"] == "A", 2, "BT,START -> A")

    print("5) Esperando EV,BT,END...")
    wait_for(lambda p: p["kind"] == "EVENT" and "BT,END" in (p["decoded"] or ""),
             120, "EV,BT,END (+ auto-ACK)")

    # 6) consultar resultado
    print("6) Consultando resultado...")
    _drain(bench.rx_queue)
    bench.send_body(f":{ADDR},BT,RES?")
    wait_for(lambda p: p["kind"] == "BT_RES", 2, "BT,RES? -> respuesta")

    print("=== SELFTEST finalizado ===\n")
    bench.print_stats()


def _drain(q):
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


def run_interactive(bench: Bench, poll_ctl: dict):
    print(HELP_TEXT)
    print("Escuchando el bus. Tipeá comandos (o 'help').\n")
    last_poll = 0.0
    # Hilo de input separado para no bloquear el polling automático
    input_q = queue.Queue()

    def _reader():
        for line in sys.stdin:
            input_q.put(line)
        input_q.put(None)  # EOF

    threading.Thread(target=_reader, daemon=True).start()

    running = True
    while running:
        # polling automático
        if poll_ctl.get("on"):
            now = time.time()
            if now - last_poll >= poll_ctl.get("interval", 2.0):
                bench.send_body(f":{ADDR},S")
                last_poll = now
        # comandos
        try:
            line = input_q.get(timeout=0.2)
        except queue.Empty:
            continue
        if line is None:
            break
        running = handle_command(bench, line, poll_ctl)


# ===========================================================================
#  MAIN
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description="Banco de pruebas RS-485 standalone")
    ap.add_argument("--port", required=True, help="Puerto serie (ej. /dev/ttyUSB0, COM3)")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--timeout", type=float, default=0.1)
    ap.add_argument("--rts-toggle", action="store_true",
                    help="Conmutar DE/RE por RTS (adaptadores sin auto-direction)")
    ap.add_argument("--no-crc", action="store_true", help="Enviar sin *HH")
    ap.add_argument("--auto-ack", dest="auto_ack", action="store_true", default=True)
    ap.add_argument("--no-auto-ack", dest="auto_ack", action="store_false",
                    help="No ACKear (para observar reintentos cada ~2 s)")
    ap.add_argument("--eol", default="crlf", choices=["crlf", "lf"])
    ap.add_argument("--logfile", help="Volcado JSONL de todas las tramas")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--poll", type=float, metavar="SEG",
                    help="Arrancar con polling automático de :01,S")
    ap.add_argument("--monitor", action="store_true",
                    help="Sniffer puro: solo escucha, no transmite")
    ap.add_argument("--selftest", action="store_true",
                    help="Corre la prueba mínima §13 y sale")
    args = ap.parse_args()

    eol = "\r\n" if args.eol == "crlf" else "\n"

    try:
        bench = Bench(
            port=args.port, baud=args.baud, timeout=args.timeout,
            rts_toggle=args.rts_toggle, with_crc=not args.no_crc,
            auto_ack=args.auto_ack, eol=eol, logfile=args.logfile,
            no_color=args.no_color,
        )
    except serial.SerialException as e:
        print(f"ERROR: no se pudo abrir {args.port}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Conectado a {args.port} @ {args.baud} 8N1 | CRC={'on' if not args.no_crc else 'off'} "
          f"| auto-ack={'on' if args.auto_ack else 'off'} | eol={args.eol}")
    bench.start()

    poll_ctl = {"on": bool(args.poll), "interval": args.poll or 2.0}

    try:
        if args.selftest:
            run_selftest(bench)
        elif args.monitor:
            print("Modo SNIFFER (no transmite). Ctrl-C para salir.\n")
            while True:
                time.sleep(0.5)
        else:
            run_interactive(bench, poll_ctl)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        bench.print_stats()
        bench.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rs485_host.py — Driver del protocolo RS-485 para la placa controladora de
                fuentes y batería, pensado para la Raspberry Pi 4B.

Deriva de:
    - Documento 1: Instructivo técnico del protocolo RS-485
    - Documento 2: Especificación de software

Características (lado host, Doc 1 §12):
    - Enlace serial 9600 8N1 half-duplex (via adaptador USB-RS485 o UART+MAX485).
    - Lectura de líneas completas hasta '\\n', ignorando '\\r'.
    - Validación y generación de CRC XOR (*HH).
    - Distinción entre respuestas inmediatas (:01,R / RS / A / CAL / BT,RES)
      y eventos confiables FIFO (:01,<id hex>,EV,...).
    - ACK inmediato (<200 ms) de eventos y deduplicación por ID.
    - Correlación comando->respuesta con timeout (bus mono-esclavo).
    - Callback de eventos para que el programa de la central se suscriba.
    - Diseñado para ejecutarse en un hilo, en paralelo con la aplicación
      principal del equipo (Abonado / Central policial).

Dependencia:  pip install pyserial
"""

from __future__ import annotations

import re
import time
import queue
import threading
import datetime as _dt
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, List

import serial  # pyserial


# --------------------------------------------------------------------------- #
#  Utilidades de protocolo
# --------------------------------------------------------------------------- #
ADDR = "01"
_EVENT_RE = re.compile(r"^[0-9A-Fa-f]{4}$")   # ID hex de 4 dígitos


def crc_xor(body: str) -> int:
    """XOR de 8 bits sobre 'body' (desde ':' hasta antes de '*'). Doc 1 §5."""
    c = 0
    for ch in body:
        c ^= ord(ch)
    return c & 0xFF


def build_frame(body: str, with_crc: bool = True) -> bytes:
    """Construye una trama lista para enviar (agrega *HH + CR LF)."""
    if with_crc:
        body = f"{body}*{crc_xor(body):02X}"
    return (body + "\r\n").encode("ascii")


# --------------------------------------------------------------------------- #
#  Modelos de datos
# --------------------------------------------------------------------------- #
@dataclass
class Status:
    """Respuesta inmediata :01,R,... (Doc 1 §9.1)."""
    ac: int
    f1_on: int
    f2_on: int
    vb_ad: int
    ib_ad: int
    ic_ad: int
    pwm: int
    fl: int                      # bit0=F1, bit1=F2
    ts: str

    @property
    def f1_fail(self) -> bool: return bool(self.fl & 0x01)
    @property
    def f2_fail(self) -> bool: return bool(self.fl & 0x02)


@dataclass
class ExtendedStatus(Status):
    """Respuesta :01,RS,... (Doc 1 §9.2)."""
    tst_act: int = 0
    tst_mode: int = 0            # 0=ninguno 1=USB 2=mensual 3=485
    tst_elapsed_s: int = 0
    last_mm: int = 0
    last_ss: int = 0


@dataclass
class Event:
    """Evento confiable FIFO (Doc 1 §10)."""
    id: str                      # ID hex, ej. '00AF'
    kind: str                    # 'AC' | 'F1' | 'F2' | 'BT'
    detail: str                  # 'OFF'|'ON'|'FAIL'|'END'
    ts: str = ""
    mode: str = ""               # solo BT,END: 'USB'|'485'|'MONTH'
    dur_mm: int = 0              # solo BT,END
    dur_ss: int = 0
    raw: str = ""


@dataclass
class Calibration:
    ad_f_min: int
    ad_f_max: int
    ad_vb_min: int
    ad_vb_test: int
    ad_vb_max: int
    ibat_500mA_ad: int


# --------------------------------------------------------------------------- #
#  Excepciones
# --------------------------------------------------------------------------- #
class RS485Timeout(Exception):
    pass


class RS485Error(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Controlador principal
# --------------------------------------------------------------------------- #
@dataclass
class _Pending:
    prefixes: List[str]
    fields: Optional[List[str]] = None
    raw: Optional[str] = None
    done: threading.Event = field(default_factory=threading.Event)


class RS485Controller:
    """
    Driver de alto nivel. Uso típico:

        ctrl = RS485Controller("/dev/ttyUSB0", on_event=mi_handler)
        ctrl.start()
        ctrl.set_datetime()          # sincroniza el reloj de la placa
        st = ctrl.poll_status()      # lee estado inmediato
        ...
        ctrl.stop()

    Es seguro para hilos: solo un comando en vuelo a la vez (bus half-duplex,
    esclavo único). Los eventos se atienden y ACKean en el hilo lector.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        response_timeout: float = 1.0,
        use_crc: bool = True,
        on_event: Optional[Callable[[Event], None]] = None,
        logger: Optional[Callable[[str], None]] = None,
        dedup_window: int = 256,
    ):
        self.port = port
        self.baudrate = baudrate
        self.response_timeout = response_timeout
        self.use_crc = use_crc
        self.on_event = on_event
        self._log = logger or (lambda m: None)

        self._ser: Optional[serial.Serial] = None
        self._reader: Optional[threading.Thread] = None
        self._running = threading.Event()

        self._tx_lock = threading.Lock()      # un comando en vuelo
        self._pending_lock = threading.Lock()
        self._pending: Optional[_Pending] = None

        self._recent_ids: "queue.deque[str]" = __import__("collections").deque(
            maxlen=dedup_window
        )
        self.events: "queue.Queue[Event]" = queue.Queue()

        # Últimos valores conocidos (útiles para la app de la central).
        self.last_status: Optional[Status] = None

    # ---------------------- ciclo de vida ---------------------- #
    def start(self) -> None:
        self._ser = serial.Serial(
            self.port, self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        self._running.set()
        self._reader = threading.Thread(
            target=self._reader_loop, name="rs485-reader", daemon=True
        )
        self._reader.start()
        self._log(f"RS-485 abierto en {self.port} @ {self.baudrate} 8N1")

    def stop(self) -> None:
        self._running.clear()
        if self._reader:
            self._reader.join(timeout=2.0)
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._log("RS-485 cerrado")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ---------------------- hilo lector ---------------------- #
    def _reader_loop(self) -> None:
        buf = bytearray()
        while self._running.is_set():
            try:
                data = self._ser.read(128)
            except Exception as e:
                self._log(f"error de lectura serial: {e}")
                time.sleep(0.2)
                continue
            if not data:
                continue
            for b in data:
                if b == 0x0D:                 # ignora CR
                    continue
                if b == 0x0A:                 # LF -> línea completa
                    line = buf.decode("ascii", errors="replace").strip()
                    buf.clear()
                    if line:
                        self._handle_line(line)
                else:
                    buf.append(b)

    def _handle_line(self, line: str) -> None:
        # 1) Verificar dirección.
        if not line.startswith(":01"):
            return
        # 2) Separar y validar CRC si existe (Doc 1 §12.1).
        body = line
        if "*" in line:
            body, _, hh = line.partition("*")
            try:
                if crc_xor(body) != int(hh, 16):
                    self._log(f"CRC inválido, descartada: {line}")
                    return          # trama corrupta: no procesar ni ACKear
            except ValueError:
                self._log(f"CRC no hexadecimal, descartada: {line}")
                return

        fields = body.split(",")
        if len(fields) < 2:
            return

        # 3) ¿Evento FIFO? (:01,<id>,EV,...) -> ACK + dedup + callback.
        if len(fields) >= 3 and _EVENT_RE.match(fields[1]) and fields[2] == "EV":
            self._handle_event(fields, line)
            return

        # 4) Respuesta inmediata: entregar al comando en espera.
        self._deliver_response(fields, line)

    # ---------------------- eventos ---------------------- #
    def _handle_event(self, fields: List[str], raw: str) -> None:
        ev_id = fields[1].upper()

        # ACK inmediato SIEMPRE (aunque sea duplicado). Doc 1 §12.3/§12.4.
        self._send_ack(ev_id)

        if ev_id in self._recent_ids:
            self._log(f"evento duplicado {ev_id}, ACK reenviado, no reprocesa")
            return
        self._recent_ids.append(ev_id)

        ev = self._parse_event(ev_id, fields, raw)
        if ev is None:
            return
        self.events.put(ev)
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception as e:
                self._log(f"error en callback de evento: {e}")

    @staticmethod
    def _parse_event(ev_id: str, f: List[str], raw: str) -> Optional[Event]:
        # f = [':01', id, 'EV', <kind>, <detail>, ...]
        if len(f) < 5:
            return None
        kind, detail = f[3], f[4]

        def ts_after() -> str:
            if "TS" in f:
                i = f.index("TS")
                if i + 1 < len(f):
                    return f[i + 1]
            return ""

        ev = Event(id=ev_id, kind=kind, detail=detail, ts=ts_after(), raw=raw)

        if kind == "BT" and detail == "END":
            # :01,id,EV,BT,END,MODE,<m>,DUR,<mm>,<ss>,TS,<ts>
            try:
                if "MODE" in f:
                    ev.mode = f[f.index("MODE") + 1]
                if "DUR" in f:
                    di = f.index("DUR")
                    ev.dur_mm = int(f[di + 1])
                    ev.dur_ss = int(f[di + 2])
            except (ValueError, IndexError):
                pass
        return ev

    def _send_ack(self, ev_id: str) -> None:
        # El ACK no espera respuesta (silencioso). Doc 1 §9.4.
        frame = build_frame(f":{ADDR},ACK,{ev_id}", with_crc=self.use_crc)
        with self._tx_lock:
            self._write(frame)

    # ---------------------- comando/respuesta ---------------------- #
    def _write(self, frame: bytes) -> None:
        assert self._ser is not None
        self._ser.write(frame)
        self._ser.flush()

    def _command(self, body: str, expect_prefixes: List[str]) -> List[str]:
        """
        Envía un comando y espera una respuesta cuyo campo[1] esté en
        expect_prefixes. Devuelve los campos. Lanza RS485Timeout/RS485Error.
        """
        pend = _Pending(prefixes=expect_prefixes)
        with self._tx_lock:                       # serializa el bus
            with self._pending_lock:
                self._pending = pend
            self._write(build_frame(body, with_crc=self.use_crc))
            got = pend.done.wait(self.response_timeout)
            with self._pending_lock:
                self._pending = None

        if not got:
            raise RS485Timeout(f"sin respuesta a: {body}")
        if pend.fields and pend.fields[1] == "A":
            code = pend.fields[2] if len(pend.fields) > 2 else "?"
            if code == "ERR":
                raise RS485Error(f"ERR en respuesta a: {body}")
            if code == "CRCERR":
                raise RS485Error(f"CRCERR en respuesta a: {body}")
        return pend.fields or []

    def _deliver_response(self, fields: List[str], raw: str) -> None:
        with self._pending_lock:
            pend = self._pending
            if pend is None:
                return
            key = fields[1]
            # 'A' (OK/ERR/CRCERR) puede satisfacer cualquier espera.
            if key in pend.prefixes or key == "A":
                pend.fields = fields
                pend.raw = raw
                self._pending = None
                pend.done.set()

    # ---------------------- API pública de alto nivel ---------------------- #
    def poll_status(self) -> Status:
        """:01,S -> :01,R,... (Doc 1 §9.1)."""
        f = self._command(f":{ADDR},S", ["R"])
        st = self._status_from_fields(f)
        self.last_status = st
        return st

    def extended_status(self) -> ExtendedStatus:
        """:01,ST -> :01,RS,... (Doc 1 §9.2)."""
        f = self._command(f":{ADDR},ST", ["RS"])
        # :01,RS,AC,F1,F2,VB,IB,IC,PWM,FL,TST_ACT,TST_MODE,ELAPSED,LMM,LSS,TS
        v = f[2:]
        return ExtendedStatus(
            ac=int(v[0]), f1_on=int(v[1]), f2_on=int(v[2]),
            vb_ad=int(v[3]), ib_ad=int(v[4]), ic_ad=int(v[5]),
            pwm=int(v[6]), fl=int(v[7]),
            tst_act=int(v[8]), tst_mode=int(v[9]),
            tst_elapsed_s=int(v[10]), last_mm=int(v[11]), last_ss=int(v[12]),
            ts=v[13] if len(v) > 13 else "",
        )

    @staticmethod
    def _status_from_fields(f: List[str]) -> Status:
        v = f[2:]   # tras ':01','R'
        return Status(
            ac=int(v[0]), f1_on=int(v[1]), f2_on=int(v[2]),
            vb_ad=int(v[3]), ib_ad=int(v[4]), ic_ad=int(v[5]),
            pwm=int(v[6]), fl=int(v[7]), ts=v[8] if len(v) > 8 else "",
        )

    def set_datetime(self, dt: Optional[_dt.datetime] = None) -> None:
        """:01,TD,YYYY,MM,DD,hh,mm,ss (Doc 1 §9.3)."""
        dt = dt or _dt.datetime.now()
        body = (f":{ADDR},TD,{dt.year:04d},{dt.month:02d},{dt.day:02d},"
                f"{dt.hour:02d},{dt.minute:02d},{dt.second:02d}")
        self._command(body, ["A"])      # espera OK (lanza si ERR)

    def calibrate(self, param: str, value: int) -> None:
        """param in {'VF','VB','IB'} (Doc 1 §9.5)."""
        if param not in ("VF", "VB", "IB"):
            raise ValueError("param debe ser VF, VB o IB")
        self._command(f":{ADDR},CAL,{param},{int(value)}", ["A"])

    def cal_show(self) -> Calibration:
        """:01,CAL,SHOW -> :01,CAL,... (Doc 1 §9.5)."""
        f = self._command(f":{ADDR},CAL,SHOW", ["CAL"])
        v = f[2:]
        return Calibration(
            ad_f_min=int(v[0]), ad_f_max=int(v[1]), ad_vb_min=int(v[2]),
            ad_vb_test=int(v[3]), ad_vb_max=int(v[4]), ibat_500mA_ad=int(v[5]),
        )

    def reset_faults(self) -> None:
        """:01,C,RST (Doc 1 §9.6)."""
        self._command(f":{ADDR},C,RST", ["A"])

    def start_battery_test(self) -> None:
        """:01,BT,START (Doc 1 §9.7). El fin llega como evento EV,BT,END."""
        self._command(f":{ADDR},BT,START", ["A"])

    def battery_test_result(self) -> Optional[Dict]:
        """
        :01,BT,RES? (Doc 1 §9.8).
        Devuelve dict {'dur_mm','dur_ss','ts'} o None si no hay resultado.
        """
        f = self._command(f":{ADDR},BT,RES?", ["BT"])
        # :01,BT,RES,OK,DUR,mm,ss,TS,<ts>   |   :01,BT,RES,NONE
        if len(f) >= 4 and f[3] == "NONE":
            return None
        res = {}
        try:
            if "DUR" in f:
                di = f.index("DUR")
                res["dur_mm"] = int(f[di + 1])
                res["dur_ss"] = int(f[di + 2])
            if "TS" in f:
                res["ts"] = f[f.index("TS") + 1]
        except (ValueError, IndexError):
            return None
        return res or None


# --------------------------------------------------------------------------- #
#  Prueba mínima por línea de comandos (Doc 1 §13)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"

    def on_ev(ev: Event):
        print(f"  [EVENTO] {ev.id} {ev.kind},{ev.detail} ts={ev.ts} "
              f"mode={ev.mode} dur={ev.dur_mm:02d}:{ev.dur_ss:02d}")

    with RS485Controller(port, on_event=on_ev, logger=print) as ctrl:
        ctrl.set_datetime()
        print("Estado:", ctrl.poll_status())
        print("Extendido:", ctrl.extended_status())
        print("Calibración:", ctrl.cal_show())
        ctrl.reset_faults()
        print("Iniciando test de batería…")
        ctrl.start_battery_test()
        # Espera el evento EV,BT,END (se ACKea solo en el hilo lector).
        deadline = time.time() + 60
        while time.time() < deadline:
            ctrl.poll_status()
            time.sleep(2)
        print("Resultado test:", ctrl.battery_test_result())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMS_test_tool.py
================
Banco de pruebas de SMS para el modem SIMCom A7670SA, a traves del bridge
USB-CDC del STM32 (por defecto /dev/ttyACM0).

Objetivo: aislar DE QUE DEPENDE el SMS, en particular la recepcion (RX),
que suele fallar de forma intermitente.

IMPORTANTE (conceptos):
  * El SMS NO usa el plan de datos, NI el APN, NI NETOPEN. Va por senializacion.
  * El SMS SI depende de: registro en red, SMSC (CSCA), servicio de SMS en la
    linea, y para RECIBIR: configuracion CNMI + almacenamiento no lleno.
  * CNMI es NO_SAVE: un CRESET o CFUN 0/1 lo borra y se dejan de ver los SMS
    entrantes. Por eso este test NO resetea el modem en el medio.

Bloques:
  0. Prep: AT, CMEE, asegurar CFUN=1, esperar registro (NO resetea)
  1. Prerrequisitos SMS: CMGF, CSCS, CSCA?, CSMP, CPMS? (storage!)
  2. (opcional) Limpieza de almacenamiento  [--clear]
  3. Configurar notificacion RX (CNMI)
  4. TX: enviar un SMS de prueba y confirmar +CMGS
  5. RX: esperar un SMS entrante (respondes desde el telefono destino)
  6. Resumen + interpretacion

Uso tipico:
  python3 SMS_test_tool.py --dest +5491156320411
  python3 SMS_test_tool.py --dest +5491156320411 --clear --wait-rx 180

Requisitos: pip install pyserial
"""

import argparse
import re
import sys
import time
from datetime import datetime

try:
    import serial
except ImportError:
    print("ERROR: falta pyserial. Instalalo con:  pip install pyserial")
    sys.exit(1)

CTRL_Z = b"\x1a"
ESC = b"\x1b"


# --------------------------------------------------------------------------- #
class ATModem:
    def __init__(self, port, baud, logpath):
        self.ser = serial.Serial(port, baud, timeout=0.1)
        self.acc = ""
        self.linebuf = ""
        self.logf = open(logpath, "w", encoding="utf-8")
        self._emit("--", f"=== SMS_test_tool  puerto={port} baud={baud} ===")

    @staticmethod
    def _ts():
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def _emit(self, direction, text):
        line = f"[{self._ts()}] {direction:<3} {text}"
        print(line)
        self.logf.write(line + "\n")
        self.logf.flush()

    def _pump(self):
        chunk = self.ser.read(512)
        if not chunk:
            return
        text = chunk.decode("utf-8", errors="replace")
        self.acc += text
        self.linebuf += text
        while "\n" in self.linebuf:
            ln, self.linebuf = self.linebuf.split("\n", 1)
            ln = ln.rstrip("\r")
            if ln != "":
                self._emit("RX", ln)

    def _flush_partial(self):
        rem = self.linebuf.strip("\r")
        if rem:
            self._emit("RX", rem)
        self.linebuf = ""

    def read_until(self, tokens, timeout):
        deadline = time.time() + timeout
        matched = None
        while time.time() < deadline:
            self._pump()
            for t in tokens:
                if t in self.acc:
                    matched = t
                    break
            if matched:
                time.sleep(0.08)
                self._pump()
                break
            time.sleep(0.02)
        self._flush_partial()
        return matched

    def send(self, cmd):
        self.acc = ""
        self._emit("TX", cmd)
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode())

    def write_raw(self, data, echo=""):
        if echo:
            self._emit("TX", echo)
        self.ser.write(data)

    def cmd(self, command, expect=("OK", "ERROR"), timeout=5):
        self.send(command)
        self.read_until(list(expect), timeout)
        return self.acc

    def wait(self, tokens, timeout):
        self.acc = ""
        return self.read_until(list(tokens), timeout), self.acc

    def wait_registered(self, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            txt = self.cmd("AT+CREG?", timeout=3)
            m = re.search(r"\+CREG:\s*\d+,(\d+)", txt)
            if m and m.group(1) in ("1", "5"):
                return True
            time.sleep(2)
        return False

    def cmgs(self, dest, body, timeout=60):
        """Envia SMS en modo texto: comando -> '>' -> texto + Ctrl+Z."""
        self.send(f'AT+CMGS="{dest}"')
        m = self.read_until([">", "ERROR"], timeout=8)
        if m != ">":
            self._emit("--", "No llego el prompt '>' para CMGS.")
            return None
        self.write_raw(body.encode() + CTRL_Z, echo=f"<sms> {body} <Ctrl+Z>")
        self.acc = ""
        self.read_until(["+CMGS:", "ERROR", "+CMS ERROR"], timeout=timeout)
        m = re.search(r"\+CMGS:\s*(\d+)", self.acc)
        return m.group(1) if m else None

    def close(self):
        try:
            self.ser.close()
        finally:
            self.logf.close()


# --------------------------------------------------------------------------- #
def parse_cpms(txt):
    """Devuelve lista de (used,total) de la respuesta +CPMS."""
    m = re.search(r"\+CPMS:\s*(.+)", txt)
    if not m:
        return []
    nums = re.findall(r"(\d+),(\d+)", m.group(1))
    return [(int(u), int(t)) for u, t in nums]


def block0_prep(m):
    m._emit("==", "BLOQUE 0 - Preparacion (SIN reset, para no borrar CNMI)")
    m.cmd("AT", timeout=3)
    m.cmd("AT+CMEE=2", timeout=3)
    txt = m.cmd("AT+CFUN?", timeout=5)
    if not re.search(r"\+CFUN:\s*1", txt):
        m._emit("--", "CFUN no era 1; activando radio full (CFUN=1).")
        m.cmd("AT+CFUN=1", timeout=10)
    reg = m.wait_registered(timeout=90)
    return {"registrado": reg}


def block1_prereq(m, results):
    m._emit("==", "BLOQUE 1 - Prerrequisitos de SMS")
    m.cmd("AT+CMGF=1", timeout=5)                 # modo texto
    m.cmd('AT+CSCS="GSM"', timeout=5)             # charset
    txt = m.cmd("AT+CSCA?", timeout=5)            # SMSC
    sca = re.search(r'\+CSCA:\s*"([^"]*)"', txt)
    results["smsc"] = sca.group(1) if sca else None
    m.cmd("AT+CSMP=17,167,0,0", timeout=5)
    txt = m.cmd("AT+CPMS?", timeout=5)            # estado de almacenamiento
    pairs = parse_cpms(txt)
    if pairs:
        results["storage_used"] = pairs[0][0]
        results["storage_total"] = pairs[0][1]
    m.cmd('AT+CPMS="SM","SM","SM"', timeout=5)    # usar SIM


def block2_clear(m, results, do_clear):
    m._emit("==", "BLOQUE 2 - Almacenamiento" + (" (limpieza)" if do_clear else ""))
    if do_clear:
        m._emit("--", "Borrando TODOS los SMS almacenados (CMGD=1,4)...")
        m.cmd("AT+CMGD=1,4", timeout=15)
        txt = m.cmd("AT+CPMS?", timeout=5)
        pairs = parse_cpms(txt)
        if pairs:
            results["storage_used_post"] = pairs[0][0]
    else:
        m._emit("--", "Sin limpieza (usa --clear para vaciar el almacenamiento).")


def block3_cnmi(m):
    m._emit("==", "BLOQUE 3 - Notificacion de SMS entrante (CNMI)")
    # 2,1 = guardar en SIM y avisar con +CMTI (patron robusto para producto)
    m.cmd("AT+CNMI=2,1,0,0,0", timeout=5)
    m.cmd("AT+CNMI?", timeout=5)


def block4_tx(m, results, dest):
    m._emit("==", "BLOQUE 4 - TX (envio de SMS)")
    stamp = datetime.now().strftime("%H:%M:%S")
    body = f"Test BKP-A7670 TX {stamp}"
    ref = m.cmgs(dest, body)
    results["tx_ref"] = ref
    results["tx_body"] = body
    if ref:
        m._emit("--", f"SMS aceptado por la red. Referencia={ref}. "
                      f"Verifica que llegue a {dest}.")
    else:
        m._emit("!!", "El envio NO fue aceptado (sin +CMGS). Revisa SMSC/registro/servicio SMS.")


def block5_rx(m, results, wait_rx):
    m._emit("==", "BLOQUE 5 - RX (recepcion de SMS)")
    m._emit("!!", f">>> AHORA responde un SMS desde el telefono destino. "
                  f"Esperando hasta {wait_rx}s... <<<")
    tok = m.read_until(["+CMTI:", "+CMT:"], timeout=wait_rx)
    if tok is None:
        results["rx_ok"] = False
        m._emit("!!", "No llego ningun SMS en la ventana de espera.")
        return
    results["rx_ok"] = True
    if "+CMTI:" in m.acc:
        # guardado en memoria: leer con CMGR
        mi = re.search(r'\+CMTI:\s*"[^"]*",(\d+)', m.acc)
        if mi:
            idx = mi.group(1)
            results["rx_index"] = idx
            m._emit("--", f"SMS recibido y guardado en indice {idx}. Leyendo...")
            m.cmd(f"AT+CMGR={idx}", timeout=8)
            # limpieza del mensaje leido
            m.cmd(f"AT+CMGD={idx}", timeout=8)
    else:
        m._emit("--", "SMS recibido directo a consola (+CMT).")


def print_summary(m, results):
    m._emit("==", "RESUMEN - Prueba de SMS")

    def yn(v):
        return "SI" if v else ("NO" if v is False else "-")

    used = results.get("storage_used")
    total = results.get("storage_total")
    storage_txt = f"{used}/{total}" if used is not None else "?"
    storage_full = (used is not None and total is not None and used >= total)

    rows = [
        ("Registrado en red",      yn(results.get("registrado"))),
        ("SMSC (CSCA)",            results.get("smsc") or "NO SETEADO"),
        ("Almacenamiento SIM",     storage_txt + ("  <-- LLENO!" if storage_full else "")),
        ("TX aceptado (ref +CMGS)", results.get("tx_ref") or "NO"),
        ("RX recibido",            yn(results.get("rx_ok"))),
    ]
    for k, v in rows:
        m._emit("  ", f"{k:<26}: {v}")

    m._emit("==", "INTERPRETACION")
    if results.get("tx_ref") and results.get("rx_ok"):
        m._emit("  ", "TX y RX funcionan. SMS operativo de punta a punta.")
    if results.get("tx_ref") and results.get("rx_ok") is False:
        m._emit("  ", "TX anda pero RX no. Causas tipicas:")
        if storage_full:
            m._emit("  ", "  -> ALMACENAMIENTO LLENO: la SIM no puede guardar entrantes.")
            m._emit("  ", "     Solucion: correr con --clear para vaciarlo.")
        m._emit("  ", "  -> CNMI borrado por un CRESET/CFUN previo (este test ya lo re-setea).")
        m._emit("  ", "  -> Verificar que el destino realmente haya respondido dentro de la ventana.")
    if not results.get("tx_ref"):
        m._emit("  ", "TX no aceptado: revisar SMSC (CSCA), registro, o servicio de SMS de la linea.")
    m._emit("  ", "Recorda: el SMS NO depende del plan de datos, del APN ni de NETOPEN.")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Banco de pruebas de SMS (TX/RX) para A7670SA via bridge.")
    ap.add_argument("--dest", required=True, help="Numero destino en formato internacional (+549...)")
    ap.add_argument("--port", default="/dev/ttyACM0", help="Puerto serie del bridge")
    ap.add_argument("--baud", type=int, default=115200, help="Baud (CDC lo ignora)")
    ap.add_argument("--wait-rx", type=int, default=120, help="Segundos a esperar un SMS entrante")
    ap.add_argument("--clear", action="store_true", help="Vaciar el almacenamiento de SMS antes de probar")
    ap.add_argument("--smsc", default=None, help="Forzar SMSC si CSCA viene vacio (ej. +54...)")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logpath = f"sms_test_{ts}.log"
    m = ATModem(args.port, args.baud, logpath)
    results = {}
    try:
        r0 = block0_prep(m)
        results.update(r0)
        if not r0.get("registrado"):
            m._emit("!!", "No registrado. El SMS necesita registro; se continua para capturar estado.")
        block1_prereq(m, results)
        if args.smsc and not results.get("smsc"):
            m._emit("--", f"Forzando SMSC a {args.smsc}")
            m.cmd(f'AT+CSCA="{args.smsc}"', timeout=5)
            results["smsc"] = args.smsc
        block2_clear(m, results, args.clear)
        block3_cnmi(m)
        block4_tx(m, results, args.dest)
        block5_rx(m, results, args.wait_rx)
        print_summary(m, results)
    except KeyboardInterrupt:
        m._emit("!!", "Interrumpido por el usuario.")
    finally:
        m._emit("--", f"Log guardado en: {logpath}")
        m.close()


if __name__ == "__main__":
    main()

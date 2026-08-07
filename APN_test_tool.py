#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APN_test_tool.py
================
Banco de pruebas automatizado para un APN en el modem SIMCom A7670SA,
a traves del bridge USB-CDC del STM32 (por defecto /dev/ttyACM0).

Ejecuta los bloques 0-7 definidos en la sesion de trabajo:
  0. Preparacion limpia (cierre + cambio de APN + CFUN 0/1)
  1. Registro y radio
  2. Apertura de datos e IP (+ DNS asignado por la red)
  3. DNS (test real de resolucion)
  4. Hora por red (NTP + HTP + NITZ)
  5. Ping (con deteccion de walled-garden)
  6. UDP con envio identificable (verdad real = log del servidor)
  7. Cierre limpio

Cada linea (TX y RX) se registra con timestamp en consola y en un archivo
apn_test_<label>_<YYYYmmdd_HHMMSS>.log

IMPORTANTE: el unico juez real de si el UDP LLEGA es el log del servidor.
El payload del bloque 6 incluye el label del APN para poder correlacionar.

Uso tipico:
  python3 APN_test_tool.py --apn igprs.claro.com.ar --label igprs
  python3 APN_test_tool.py --apn eysem2m.claro.com.ar --label eysem2m

Requisitos: pip install pyserial
"""

import argparse
import re
import sys
import time
from datetime import datetime

try:
    import serial  # pyserial
except ImportError:
    print("ERROR: falta pyserial. Instalalo con:  pip install pyserial")
    sys.exit(1)


# --------------------------------------------------------------------------- #
#  Capa de comunicacion con el modem
# --------------------------------------------------------------------------- #
class ATModem:
    def __init__(self, port, baud, logpath):
        self.ser = serial.Serial(port, baud, timeout=0.1)
        self.acc = ""        # acumulador crudo desde el ultimo comando (scan de tokens)
        self.linebuf = ""    # buffer de linea parcial (para logging)
        self.logf = open(logpath, "w", encoding="utf-8")
        self._emit("--", f"=== APN_test_tool  puerto={port} baud={baud} ===")

    # ---- logging ---------------------------------------------------------- #
    @staticmethod
    def _ts():
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def _emit(self, direction, text):
        line = f"[{self._ts()}] {direction:<3} {text}"
        print(line)
        self.logf.write(line + "\n")
        self.logf.flush()

    # ---- bajo nivel ------------------------------------------------------- #
    def _pump(self):
        """Lee lo que haya disponible, lo acumula y emite lineas completas."""
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
        """Emite lo que quedo en el buffer de linea sin '\\n' (ej. el prompt '>')."""
        rem = self.linebuf.strip("\r")
        if rem:
            self._emit("RX", rem)
        self.linebuf = ""

    def read_until(self, tokens, timeout):
        """Lee hasta encontrar cualquier substring de 'tokens' o agotar timeout.
        Devuelve el token que matcheo (o None)."""
        deadline = time.time() + timeout
        matched = None
        while time.time() < deadline:
            self._pump()
            for t in tokens:
                if t in self.acc:
                    matched = t
                    break
            if matched:
                # pequena espera para capturar lineas de la misma rafaga
                time.sleep(0.08)
                self._pump()
                break
            time.sleep(0.02)
        self._flush_partial()
        return matched

    def send(self, cmd):
        """Envia un comando AT (agrega CR) y resetea el acumulador."""
        self.acc = ""
        self._emit("TX", cmd)
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode())

    def cmd(self, command, expect=("OK", "ERROR"), timeout=5):
        """Envia y espera OK/ERROR (o los tokens dados). Devuelve texto acumulado."""
        self.send(command)
        self.read_until(list(expect), timeout)
        return self.acc

    def wait(self, tokens, timeout):
        """Espera URC(s) sin enviar comando (resetea acumulador primero)."""
        self.acc = ""
        self.read_until(list(tokens), timeout)
        return self.acc

    # ---- helpers de alto nivel ------------------------------------------- #
    def wait_registered(self, timeout=60):
        """Poll CREG hasta stat 1 (home) o 5 (roaming)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            txt = self.cmd("AT+CREG?", timeout=3)
            m = re.search(r"\+CREG:\s*\d+,(\d+)", txt)
            if m and m.group(1) in ("1", "5"):
                return True
            time.sleep(2)
        return False

    def netopen(self, settle):
        """Abre la red de datos. Chequea estado primero para evitar 'already opened'."""
        txt = self.cmd("AT+NETOPEN?", timeout=3)
        if re.search(r"\+NETOPEN:\s*1", txt):
            self._emit("--", "Red ya estaba abierta.")
            return True
        self.send("AT+NETOPEN")
        self.read_until(["+NETOPEN: 0", "+NETOPEN:", "ERROR"], timeout=30)
        ok = "+NETOPEN: 0" in self.acc
        if ok and settle > 0:
            self._emit("--", f"Esperando {settle}s de estabilizacion (A7670SA)...")
            time.sleep(settle)
        return ok

    def cipsend(self, link, payload, ip, port, resp_timeout=15):
        """Envio UDP con la forma de longitud entrecomillada.
        Espera '>', manda exactamente N bytes, captura +CIPSEND y RECV FROM."""
        data = payload.encode()
        n = len(data)
        self.send(f'AT+CIPSEND={link},{n},"{ip}",{port}')
        m = self.read_until([">", "ERROR"], timeout=5)
        if m != ">":
            self._emit("--", "No llego el prompt '>' para CIPSEND.")
            return {"prompt": False, "cipsend": False, "recv": False}
        self._emit("TX", f"<payload {n}B> {payload}")
        self.ser.write(data)  # sin CR ni Ctrl-Z: el modem envia al completar N bytes
        self.read_until([f"+CIPSEND: {link},", "ERROR", "+CIPERROR"], timeout=resp_timeout)
        cipsend_ok = f"+CIPSEND: {link}," in self.acc
        # respuesta del servidor (echo) si la hay
        self.acc = ""
        self.read_until(["RECV FROM", "+IPD"], timeout=6)
        recv = "RECV FROM" in self.acc or "+IPD" in self.acc
        return {"prompt": True, "cipsend": cipsend_ok, "recv": recv}

    def close(self):
        try:
            self.ser.close()
        finally:
            self.logf.close()


# --------------------------------------------------------------------------- #
#  Bloques de prueba
# --------------------------------------------------------------------------- #
def block0_prepare(m, apn):
    m._emit("==", "BLOQUE 0 - Preparacion limpia + cambio de APN")
    m.cmd("AT", timeout=3)
    m.cmd("AT+CMEE=2", timeout=3)
    m.cmd("AT+CIPCLOSE=0", timeout=5)
    m.send("AT+NETCLOSE")
    m.read_until(["+NETCLOSE:", "OK", "ERROR"], timeout=10)
    m.cmd(f'AT+CGDCONT=1,"IP","{apn}"', timeout=5)
    m.send("AT+CFUN=0")
    m.read_until(["OK", "ERROR"], timeout=10)
    m._emit("--", "Esperando 3s tras CFUN=0...")
    time.sleep(3)
    m.send("AT+CFUN=1")
    m.read_until(["OK", "ERROR"], timeout=10)
    m._emit("--", "Esperando registro tras CFUN=1...")
    reg = m.wait_registered(timeout=90)
    return {"registrado": reg}


def block1_radio(m, results):
    m._emit("==", "BLOQUE 1 - Registro y radio")
    m.cmd("AT+CPIN?", timeout=5)
    m.cmd("AT+CICCID", timeout=5)   # ID de la SIM (extra A)
    m.cmd("AT+CIMI", timeout=5)
    m.cmd("AT+CSQ", timeout=5)
    m.cmd("AT+CREG?", timeout=5)
    m.cmd("AT+CGREG?", timeout=5)
    m.cmd("AT+CEREG?", timeout=5)
    txt = m.cmd("AT+COPS?", timeout=8)
    m.cmd("AT+CPSI?", timeout=8)
    m.cmd("AT+CGATT?", timeout=5)
    # tecnologia aproximada desde COPS (ultimo campo: 0=GSM,2=GSM/EGPRS,7=LTE)
    act = re.search(r"\+COPS:\s*\d+,\d+,\"[^\"]*\",(\d+)", txt)
    results["tecnologia_cops"] = act.group(1) if act else "?"


def block2_data(m, results, settle):
    m._emit("==", "BLOQUE 2 - Apertura de datos e IP")
    opened = m.netopen(settle)
    results["netopen"] = opened
    txt = m.cmd("AT+IPADDR", timeout=5)
    ipm = re.search(r"\+IPADDR:\s*([0-9.]+)", txt)
    results["ip"] = ipm.group(1) if ipm else None
    # DNS asignado por la red
    txt = m.cmd("AT+CGCONTRDP=1", timeout=8)
    dns = re.findall(r"(\d{1,3}(?:\.\d{1,3}){3})", txt)
    # los primeros son APN/IP; los DNS suelen ser el 3er/4to octeto-grupo -> guardamos todos
    results["cgcontrdp_ips"] = dns


def block3_dns(m, results, host):
    m._emit("==", "BLOQUE 3 - DNS")
    m.cmd("AT+CDNSCFG?", timeout=5)
    txt = m.cmd(f'AT+CDNSGIP="{host}"', expect=("+CDNSGIP:", "ERROR"), timeout=12)
    ok = bool(re.search(r"\+CDNSGIP:\s*1,", txt))
    results["dns_resuelve"] = ok
    if not ok:
        # segundo intento forzando 8.8.8.8 para distinguir bloqueo de APN vs resolver
        m.cmd('AT+CDNSCFG="8.8.8.8","1.1.1.1",0', timeout=5)
        txt = m.cmd(f'AT+CDNSGIP="{host}"', expect=("+CDNSGIP:", "ERROR"), timeout=12)
        results["dns_resuelve_8888"] = bool(re.search(r"\+CDNSGIP:\s*1,", txt))


def block4_time(m, results, ntp_host, tz):
    m._emit("==", "BLOQUE 4 - Hora por red (NTP / HTP / NITZ)")
    m.cmd("AT+CCLK?", timeout=5)
    m.cmd("AT+CTZU=1", timeout=5)
    m.cmd("AT+CTZR=1", timeout=5)
    # NTP
    m.cmd(f'AT+CNTP="{ntp_host}",{tz}', timeout=5)
    m.send("AT+CNTP")
    m.read_until(["+CNTP:", "ERROR"], timeout=15)
    mntp = re.search(r"\+CNTP:\s*(\d+)", m.acc)
    results["ntp"] = mntp.group(1) if mntp else "?"   # 0=exito, 6=timeout
    # HTP (host SIN formato de link)
    m.cmd('AT+CHTPSERV="ADD","www.google.com",80,1', timeout=5)
    m.send("AT+CHTPUPDATE")
    m.read_until(["+CHTPUPDATE:", "ERROR"], timeout=15)
    mhtp = re.search(r"\+CHTPUPDATE:\s*(\d+)", m.acc)
    results["htp"] = mhtp.group(1) if mhtp else "?"    # 0=exito
    txt = m.cmd("AT+CCLK?", timeout=5)
    results["reloj_post"] = re.search(r"\+CCLK:\s*\"([^\"]+)\"", txt)
    results["reloj_post"] = results["reloj_post"].group(1) if results["reloj_post"] else "?"


def block5_ping(m, results, server_ip):
    m._emit("==", "BLOQUE 5 - Ping (ICMP, orientativo)")
    m.send(f'AT+CPING="{server_ip}",1,4')
    m.read_until(["+CPING: 3,", "ERROR"], timeout=45)
    # resumen
    summ = re.search(r"\+CPING:\s*3,(\d+),(\d+),(\d+)", m.acc)
    if summ:
        results["ping_sent"], results["ping_recv"], results["ping_lost"] = summ.groups()
    # de que IP vino la respuesta (walled-garden si != server_ip)
    replies = re.findall(r"\+CPING:\s*1,([0-9.]+),", m.acc)
    results["ping_reply_ips"] = list(set(replies))
    if replies and any(ip != server_ip for ip in replies):
        m._emit("!!", f"OJO: respuesta de ping desde {set(replies)} (no {server_ip}) "
                       f"-> posible walled-garden.")


def block6_udp(m, results, server_ip, server_port, label):
    m._emit("==", "BLOQUE 6 - UDP (verdad real = log del servidor)")
    txt = m.cmd(f'AT+CIPOPEN=0,"UDP","{server_ip}",{server_port},0',
                expect=("+CIPOPEN: 0,", "ERROR"), timeout=10)
    results["cipopen_ok"] = "+CIPOPEN: 0,0" in txt
    stamp = datetime.now().strftime("%H%M%S")
    payload = f"TEST {label} {stamp}"
    r = m.cipsend(0, payload, server_ip, server_port)
    results["cipsend_ok"] = r["cipsend"]
    results["recv_from"] = r["recv"]
    results["payload_enviado"] = payload
    m._emit("--", f"Correlacionar en el servidor el payload: '{payload}'")


def block7_close(m):
    m._emit("==", "BLOQUE 7 - Cierre limpio")
    m.cmd("AT+CIPCLOSE=0", timeout=5)
    m.send("AT+NETCLOSE")
    m.read_until(["+NETCLOSE:", "OK", "ERROR"], timeout=10)


# --------------------------------------------------------------------------- #
#  Resumen final
# --------------------------------------------------------------------------- #
def print_summary(m, results, label):
    m._emit("==", f"RESUMEN - APN '{label}'")

    def yn(v):
        return "SI" if v else "NO"

    reg = results.get("registrado")
    tec = results.get("tecnologia_cops", "?")
    tec_txt = {"0": "GSM", "2": "GSM/EGPRS(2G)", "7": "LTE"}.get(tec, tec)
    ntp = results.get("ntp", "?")
    htp = results.get("htp", "?")

    rows = [
        ("Registrado en red",        yn(reg)),
        ("Tecnologia (COPS)",        tec_txt),
        ("NETOPEN",                  yn(results.get("netopen"))),
        ("IP asignada",              results.get("ip") or "NO"),
        ("DNS resuelve (red)",       yn(results.get("dns_resuelve"))),
        ("DNS resuelve (8.8.8.8)",   yn(results.get("dns_resuelve_8888")) if "dns_resuelve_8888" in results else "-"),
        ("NTP  (0=ok / 6=timeout)",  ntp),
        ("HTP  (0=ok)",              htp),
        ("Reloj tras hora",          results.get("reloj_post", "?")),
        ("Ping recv/sent",           f"{results.get('ping_recv','?')}/{results.get('ping_sent','?')}"),
        ("Ping responde IP",         ", ".join(results.get("ping_reply_ips", [])) or "-"),
        ("CIPOPEN UDP",              yn(results.get("cipopen_ok"))),
        ("CIPSEND (modem->red)",     yn(results.get("cipsend_ok"))),
        ("RECV FROM (server echo)",  yn(results.get("recv_from"))),
    ]
    for k, v in rows:
        m._emit("  ", f"{k:<26}: {v}")

    m._emit("!!", "RECORDA: 'CIPSEND ok' NO significa que el paquete llego al servidor.")
    m._emit("!!", f"La entrega REAL se confirma buscando '{results.get('payload_enviado','TEST ...')}' "
                  f"en el log del servidor.")


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Banco de pruebas de APN para A7670SA via bridge.")
    ap.add_argument("--apn", required=True, help="APN a probar (ej. igprs.claro.com.ar)")
    ap.add_argument("--label", default=None, help="Etiqueta para el payload/log (default = apn)")
    ap.add_argument("--port", default="/dev/ttyACM0", help="Puerto serie del bridge (default /dev/ttyACM0)")
    ap.add_argument("--baud", type=int, default=115200, help="Baud (CDC lo ignora; default 115200)")
    ap.add_argument("--server-ip", default="190.111.217.188", help="IP del servidor UDP")
    ap.add_argument("--server-port", type=int, default=57777, help="Puerto del servidor UDP")
    ap.add_argument("--host", default="dgranz.is-an-engineer.com", help="Hostname para test DNS")
    ap.add_argument("--ntp", default="time.windows.com", help="Servidor NTP")
    ap.add_argument("--tz", type=int, default=-12, help="Timezone en cuartos de hora (Argentina = -12)")
    ap.add_argument("--settle", type=int, default=25, help="Segundos de estabilizacion tras NETOPEN")
    args = ap.parse_args()

    label = args.label or args.apn.split(".")[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logpath = f"apn_test_{label}_{ts}.log"

    m = ATModem(args.port, args.baud, logpath)
    results = {}
    try:
        r0 = block0_prepare(m, args.apn)
        results.update(r0)
        if not r0.get("registrado"):
            m._emit("!!", "No registro en la red. Se continua igual para capturar el estado.")
        block1_radio(m, results)
        block2_data(m, results, args.settle)
        if results.get("netopen"):
            block3_dns(m, results, args.host)
            block4_time(m, results, args.ntp, args.tz)
            block5_ping(m, results, args.server_ip)
            block6_udp(m, results, args.server_ip, args.server_port, label)
        else:
            m._emit("!!", "NETOPEN fallo: se saltan bloques 3-6 (dependen de datos).")
        block7_close(m)
        print_summary(m, results, label)
    except KeyboardInterrupt:
        m._emit("!!", "Interrumpido por el usuario.")
    finally:
        m._emit("--", f"Log guardado en: {logpath}")
        m.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cfg_tool.py
-----------
Herramienta de consola para configurar/diagnosticar el equipo BKP-A7670SA
(STM32F411 + A7670SA) a través del protocolo de texto $CFG.

Pensado para Debian 12 (Bookworm) en una Raspberry Pi conectada por USB
al STM32 (puerto CDC virtual, típicamente /dev/ttyACM0).

Requisitos:
    pip3 install pyserial --break-system-packages

Notas de protocolo (verificadas contra main.c):
  - Formato general de trama:
        $CFG,<CMD>,<PARAM1>,...,<PASSWORD>*<CRC16_HEX_4_DIGITOS>\r\n
  - El CRC16-CCITT se calcula sobre el texto desde '$' hasta el carácter
    inmediatamente anterior a '*' (sin incluir el '*' ni lo que sigue).
  - El password SIEMPRE es el último campo antes del '*'.
  - WRITE no lleva un campo de longitud: $CFG,WRITE,{json},password*CRC
  - El equipo solo entra a modo CONFIG cuando, estando en modo NORMAL,
    detecta una línea que contiene "$CFG". Esa primera línea se descarta
    (no se procesa como comando real). La opción 11 (Conectar) expone esto
    explícitamente: manda esa línea y espera el texto "[MODO] CONFIG".
  - La opción 12 (Exit) manda "$CFG,EXIT,<password>*CRC". El firmware
    reconoce el prefijo "$CFG,EXIT" en CFG_RECEIVE_FRAME y corta directo a
    modo NORMAL sin validar password ni CRC (por diseño: salir no modifica
    nada). El script igual arma la trama completa por prolijidad/consistencia
    con el resto de los comandos, aunque el firmware la ignore.
  - MODEMREINIT responde actualmente "$CFG,ACK,REBOOT,OK" en el firmware
    (no "MODEMREINIT,OK"). El script lo contempla como éxito válido.
  - La opción 10 (Salir) es puramente local: cierra el puerto serie y
    termina el script. No manda nada al equipo. Para dejar el equipo en
    modo NORMAL antes de salir, usar la opción 12 (Exit).
  - El script YA NO fuerza el modo CONFIG al arrancar. Si el equipo está
    en modo NORMAL, hay que usar la opción 11 (Conectar) antes de operar.
"""

import sys
import os
import json
import time
import shutil
from datetime import datetime

try:
    import serial
except ImportError:
    print("Falta el módulo pyserial. Instalalo con:")
    print("    pip3 install pyserial --break-system-packages")
    sys.exit(1)

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

# El firmware procesa los comandos $CFG sobre el puerto CDC-USB del STM32
# (usbBuffer), que en la Raspberry Pi aparece como /dev/ttyACM0.
# Si en tu instalación el CDC aparece con otro nombre, cambialo acá.
SERIAL_PORT = "/dev/ttyACM0"
BAUDRATE = 9600

# El CDC-USB del STM32 no es una UART física real: el framing (bits/paridad/
# stop) no tiene efecto en el enlace, pero pyserial igual exige abrir el
# puerto con algún valor. Se deja 8N1 por default. Si en algún momento se
# conecta el script a la UART1 física (RS232 hacia el panel/servidor de
# config), esa sí es 8E2 real y habría que cambiar estos tres valores.
BYTESIZE = serial.EIGHTBITS
PARITY = serial.PARITY_NONE
STOPBITS = serial.STOPBITS_ONE

RESPONSE_TIMEOUT = 2.5  # segundos, según lo especificado

CONFIG_PATH = "/home/pi/config_modem.json"

# ============================================================================
# CRC16-CCITT (idéntico al usado en el firmware)
# ============================================================================


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8) & 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def build_frame(fields):
    """fields: lista de strings/números, el primero debe ser '$CFG'."""
    body = ",".join(str(f) for f in fields)
    crc = crc16_ccitt(body.encode("utf-8"))
    return f"{body}*{crc:04X}\r\n".encode("utf-8")


def verify_crc(text):
    """Devuelve (body_sin_crc, crc_ok: bool). body es None si no hay '*'."""
    text = text.strip()
    if "*" not in text:
        return None, False
    body, _, tail = text.partition("*")
    hexdigits = "0123456789abcdefABCDEF"
    crc_hex = "".join(ch for ch in tail if ch in hexdigits)[:4]
    if len(crc_hex) < 4:
        return body, False
    try:
        crc_rx = int(crc_hex, 16)
    except ValueError:
        return body, False
    crc_calc = crc16_ccitt(body.encode("utf-8"))
    return body, (crc_calc == crc_rx)


# ============================================================================
# CAPA DE COMUNICACIÓN
# ============================================================================


def read_response(ser, timeout=RESPONSE_TIMEOUT):
    end = time.time() + timeout
    buf = bytearray()
    ser.timeout = 0.05
    while time.time() < end:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            if b"\n" in buf:
                break
    return bytes(buf)


def send_and_receive(ser, fields, timeout=RESPONSE_TIMEOUT):
    frame = build_frame(fields)
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.write(frame)
    raw = read_response(ser, timeout)
    text = raw.decode("utf-8", errors="replace")
    body, crc_ok = verify_crc(text)
    return text, body, crc_ok


def wait_for_marker(ser, marker, timeout=RESPONSE_TIMEOUT):
    """
    Espera hasta encontrar 'marker' (texto plano, sin CRC) en lo que llega
    por el puerto, por ejemplo "[MODO] CONFIG" o "[MODO] NORMAL". Estos
    mensajes vienen de Debug_Print() en el firmware, no son tramas $CFG con
    CRC, así que se buscan como substring simple.
    Devuelve (encontrado: bool, texto_crudo_acumulado: str).
    """
    end = time.time() + timeout
    buf = bytearray()
    ser.timeout = 0.05
    marker_bytes = marker.encode("utf-8")
    while time.time() < end:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            if marker_bytes in buf:
                return True, bytes(buf).decode("utf-8", errors="replace")
    return False, bytes(buf).decode("utf-8", errors="replace")


# ============================================================================
# TABLAS DE INTERPRETACIÓN DE RESPUESTAS
# ============================================================================

ACK_MEANINGS = {
    "WRITE": "Configuración recibida",
    "SAVE": "Grabada en FLASH",
    "DEFAULT": "Configuración de fábrica",
    "REBOOT": "Reset STM32F",
    "MODEMREINIT": "Reset del Módulo BKP-A7670SA",
    "LOGCLEAR": "Log borrado",
}

ERROR_MEANINGS = {
    "PASSWORD": "Password incorrecta",
    "CRC": "CRC incorrecto",
    "JSON": "JSON inválido",
    "FLASH": "Error de escritura FLASH",
    "MODE": "No está en modo configuración",
    "FORMAT": "Trama inválida",
    "TIMEOUT": "Timeout recepción",
    "LOGINDEX": "Log Index fuera de rango",
    "UNKNOWN": "Error genérico",
    "INVALIDO": "Comando no reconocido por el firmware",
}


def print_result(raw_text, body, crc_ok, sent_cmd=None):
    """
    Imprime la respuesta cruda y su interpretación.
    Devuelve (kind, code, parts) con kind en {"ACK","ERROR","DATA","UNKNOWN","TIMEOUT"}.
    """
    if not raw_text or not raw_text.strip():
        print("\n<-- (sin respuesta)")
        print("*** TIMEOUT: el equipo no respondió dentro de los "
              f"{RESPONSE_TIMEOUT}s esperados. ***")
        return ("TIMEOUT", None, None)

    print(f"\n<-- Respuesta cruda: {raw_text.strip()}")

    if body is None:
        print("*** Trama sin '*CRC' reconocible. ***")
        return ("UNKNOWN", None, None)

    if not crc_ok:
        print("*** ADVERTENCIA: el CRC de la respuesta no coincide "
              "(posible trama corrupta). ***")

    # DATA (respuesta de READ) puede tener comas dentro del JSON,
    # así que se parsea aparte, sin split() genérico.
    if body.startswith("$CFG,DATA,"):
        rest = body[len("$CFG,DATA,"):]
        len_str, _, json_str = rest.partition(",")
        print(f"OK -> Configuración recibida (longitud declarada: {len_str})")
        return ("DATA", json_str, None)

    parts = body.split(",")

    if len(parts) >= 3 and parts[0] == "$CFG" and parts[1] == "ACK":
        ack_cmd = parts[2]
        if sent_cmd == "MODEMREINIT" and ack_cmd == "REBOOT":
            print("OK -> Reset del Módulo BKP-A7670SA "
                  "(nota: el firmware responde 'ACK,REBOOT' para este comando)")
        else:
            meaning = ACK_MEANINGS.get(ack_cmd, f"ACK de {ack_cmd} (sin descripción)")
            print(f"OK -> {meaning}")
        return ("ACK", ack_cmd, parts)

    if len(parts) >= 3 and parts[0] == "$CFG" and parts[1] == "ERROR":
        err = parts[2]
        meaning = ERROR_MEANINGS.get(err, "Error desconocido")
        print(f"ERROR -> {meaning}")
        return ("ERROR", err, parts)

    print("Respuesta no reconocida por este script.")
    return ("UNKNOWN", None, parts)


# ============================================================================
# UTILIDADES DE ENTRADA POR TECLADO
# ============================================================================


def ask_password(prompt="Ingrese los 4 dígitos de password de configuración: "):
    while True:
        pwd = input(prompt).strip()
        if len(pwd) == 4 and pwd.isdigit():
            return pwd
        print("  El password debe ser exactamente 4 dígitos numéricos.")


def ask_int(prompt, min_value=0):
    while True:
        raw = input(prompt).strip()
        try:
            value = int(raw)
            if value < min_value:
                raise ValueError
            return value
        except ValueError:
            print(f"  Ingrese un número entero >= {min_value}.")


# ============================================================================
# OPCIONES DE MENÚ
# ============================================================================


def opt_read(ser):
    print("\n--- Leer configuración ---")
    pwd = ask_password()
    text, body, crc_ok = send_and_receive(ser, ["$CFG", "READ", pwd])
    kind, payload, _ = print_result(text, body, crc_ok, sent_cmd="READ")

    if kind != "DATA":
        return

    json_str = payload
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"*** El JSON recibido no se pudo parsear: {e} ***")
        print("JSON crudo recibido:")
        print(json_str)
        return

    print("\nConfiguración actual del equipo:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    resp = input(
        f"\n¿Guardar esta configuración en {CONFIG_PATH}? (s/N): "
    ).strip().lower()
    if resp == "s":
        save_local_config(data)


def save_local_config(data):
    try:
        if os.path.exists(CONFIG_PATH):
            backup = CONFIG_PATH + ".bak." + datetime.now().strftime("%Y%m%d%H%M%S")
            shutil.copy2(CONFIG_PATH, backup)
            print(f"  Backup del archivo anterior: {backup}")
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"  Guardado en {CONFIG_PATH}")
    except OSError as e:
        print(f"*** No se pudo escribir {CONFIG_PATH}: {e} ***")


def load_local_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"*** No existe {CONFIG_PATH}. Abortando esta operación. ***")
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"*** Error leyendo {CONFIG_PATH}: {e} ***")
        return None


def opt_write(ser):
    print("\n--- Escribir configuración ---")
    data = load_local_config()
    if data is None:
        return

    data["config_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_local_config(data)

    print("\nConfiguración a enviar:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    # Nota: si esta JSON viene de un READ previo, su clave "cfg_password"
    # NO actualiza runtime_cfg.config_password en el firmware actual
    # (Parse_JSON_To_Runtime_Config busca la clave "config_password").
    # Es un desalineamiento entre Build_Config_JSON() y
    # Parse_JSON_To_Runtime_Config() en main.c, no un bug de este script.
    if "cfg_password" in data:
        print("\n*** Aviso: el firmware actual no toma el password nuevo desde "
              "la clave 'cfg_password' al escribir (busca 'config_password'). "
              "El password de configuración del equipo NO cambiará con este "
              "WRITE aunque el JSON tenga 'cfg_password' distinto. ***")

    json_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    pwd = ask_password(
        "\nIngrese el password de configuración ACTUAL del equipo "
        "(el que ya tiene cargado, para autenticar el WRITE): "
    )

    text, body, crc_ok = send_and_receive(
        ser, ["$CFG", "WRITE", json_str, pwd]
    )
    print_result(text, body, crc_ok, sent_cmd="WRITE")


def opt_simple_password_command(ser, cmd, label):
    print(f"\n--- {label} ---")
    pwd = ask_password()
    text, body, crc_ok = send_and_receive(ser, ["$CFG", cmd, pwd])
    print_result(text, body, crc_ok, sent_cmd=cmd)


def opt_loginfo(ser):
    print("\n--- Información del LOG ---")
    pwd = ask_password()
    text, body, crc_ok = send_and_receive(ser, ["$CFG", "LOGINFO", pwd])
    kind, code, parts = print_result(text, body, crc_ok, sent_cmd="LOGINFO")
    if kind == "ACK" and code == "LOGINFO" and parts and len(parts) >= 5:
        count, head = parts[3], parts[4]
        print(f"  Registros válidos almacenados (count): {count}")
        print(f"  Próximo índice de escritura (head)    : {head}")


def read_one_log_record(ser, index, pwd):
    """
    Manda un único $CFG,LOGREAD,<index>,<password>*CRC y muestra el
    resultado con el mismo formato que la opción 8. Devuelve (kind, code,
    parts) tal como print_result().
    """
    text, body, crc_ok = send_and_receive(ser, ["$CFG", "LOGREAD", index, pwd])
    kind, code, parts = print_result(text, body, crc_ok, sent_cmd="LOGREAD")
    if kind == "ACK" and code == "LOGREAD" and parts and len(parts) >= 8:
        _, _, _, idx, timestamp, event, p1, p2 = parts[:8]
        print(f"  Índice     : {idx}")
        print(f"  Timestamp  : {timestamp}")
        print(f"  Evento     : {event}")
        print(f"  Param 1    : {p1}")
        print(f"  Param 2    : {p2}")
    return kind, code, parts


def opt_logread(ser):
    print("\n--- Lectura del LOG ---")
    index = ask_int("Índice de registro a leer: ", min_value=0)
    pwd = ask_password()
    read_one_log_record(ser, index, pwd)


def opt_logread_range(ser):
    print("\n--- Lectura de rango de registros de LOG ---")
    start = ask_int("Índice inicial: ", min_value=0)
    end = ask_int(f"Índice final (>= {start}): ", min_value=start)
    pwd = ask_password()

    total = end - start + 1
    print(f"\nLeyendo {total} registro(s), de {start} a {end}...")

    ok_count = 0
    err_count = 0
    timeout_count = 0

    for index in range(start, end + 1):
        print(f"\n=== Registro {index} ({index - start + 1}/{total}) ===")
        kind, code, _ = read_one_log_record(ser, index, pwd)
        if kind == "ACK":
            ok_count += 1
        elif kind == "ERROR":
            err_count += 1
        elif kind == "TIMEOUT":
            timeout_count += 1

    print(f"\n--- Fin del rango: {ok_count} OK, {err_count} error(es), "
          f"{timeout_count} timeout(s) de {total} solicitados ---")


def opt_exit(ser):
    print("\nCerrando conexión y saliendo del script.")
    print("(Esto solo cierra el puerto serie local; el equipo permanece en "
          "el modo en que estaba. Usar la opción 12 si querés que el equipo "
          "confirme la vuelta a modo NORMAL antes de salir.)")


def opt_connect(ser):
    """
    Opción 11: fuerza al equipo a entrar en modo CONFIG.
    Basta con que la línea contenga "$CFG" (no requiere trama completa
    con password/CRC); el firmware lo detecta en modo NORMAL y responde
    "[MODO] CONFIG". Si el equipo ya estaba en CONFIG, esta misma línea
    (sin coma tras "$CFG") falla el chequeo de formato y responde
    "$CFG,ERROR,FORMAT", lo cual también confirma que ya está conectado.
    """
    print("\n--- Conectar ---")
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.write(b"$CFG\r\n")
    raw = read_response(ser, timeout=RESPONSE_TIMEOUT)
    text = raw.decode("utf-8", errors="replace")
    stripped = text.strip()
    print(f"\n<-- Respuesta cruda: {stripped if stripped else '(sin respuesta)'}")

    if "[MODO] CONFIG" in text:
        print("Conectado.")
    elif "$CFG,ERROR,FORMAT" in text:
        print("El equipo ya estaba en modo CONFIG. Conectado.")
    else:
        print("*** No se recibió confirmación de conexión "
              f"dentro de los {RESPONSE_TIMEOUT}s. ***")


def opt_cfg_exit(ser):
    """
    Opción 12: $CFG,EXIT,<password>*CRC
    El firmware reconoce el prefijo "$CFG,EXIT" (9 caracteres) apenas
    termina de recibir la línea, sin validar password ni CRC (según el
    comentario del firmware: salir no modifica nada y no necesita esa
    verificación). Responde "[MODO] NORMAL" en texto plano, no es una
    trama $CFG,ACK/ERROR con CRC. El password se pide solo para mantener
    el formato de trama consistente con el resto de los comandos.
    """
    print("\n--- Exit (volver a modo NORMAL) ---")
    pwd = ask_password()
    frame = build_frame(["$CFG", "EXIT", pwd])
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.write(frame)
    raw = read_response(ser, timeout=RESPONSE_TIMEOUT)
    text = raw.decode("utf-8", errors="replace")
    stripped = text.strip()
    print(f"\n<-- Respuesta cruda: {stripped if stripped else '(sin respuesta)'}")

    if "[MODO] NORMAL" in text:
        print("El equipo volvió a modo NORMAL.")
    else:
        print("*** No se recibió confirmación de modo NORMAL "
              f"dentro de los {RESPONSE_TIMEOUT}s. ***")


# ============================================================================
# MENÚ PRINCIPAL
# ============================================================================

MENU_TEXT = """
==================== BKP-A7670SA - Configuración ====================
 1) Leer configuración
 2) Escribir configuración
 3) Guardar en FLASH
 4) Restaurar valores de fábrica
 5) Reiniciar STM32
 6) Reiniciar BKP-A7670SA (módem)
 7) Información del LOG
 8) Lectura del LOG
 9) Borrado del LOG
10) Salir
11) Conectar
12) Exit (volver a modo NORMAL)
13) Leer rango de registros de LOG
=======================================================================
"""


def main():
    print(f"Abriendo {SERIAL_PORT} @ {BAUDRATE} bps...")
    try:
        ser = serial.Serial(
            port=SERIAL_PORT,
            baudrate=BAUDRATE,
            bytesize=BYTESIZE,
            parity=PARITY,
            stopbits=STOPBITS,
            timeout=0.1,
        )
    except serial.SerialException as e:
        print(f"*** No se pudo abrir {SERIAL_PORT}: {e} ***")
        sys.exit(1)

    try:
        while True:
            print(MENU_TEXT)
            choice = input("Seleccione una opción: ").strip()

            if choice == "1":
                opt_read(ser)
            elif choice == "2":
                opt_write(ser)
            elif choice == "3":
                opt_simple_password_command(ser, "SAVE", "Guardar en FLASH")
            elif choice == "4":
                opt_simple_password_command(
                    ser, "DEFAULT", "Restaurar valores de fábrica"
                )
            elif choice == "5":
                opt_simple_password_command(ser, "REBOOT", "Reiniciar STM32")
            elif choice == "6":
                opt_simple_password_command(
                    ser, "MODEMREINIT", "Reiniciar BKP-A7670SA (módem)"
                )
            elif choice == "7":
                opt_loginfo(ser)
            elif choice == "8":
                opt_logread(ser)
            elif choice == "9":
                opt_simple_password_command(ser, "LOGCLEAR", "Borrado del LOG")
            elif choice == "10":
                opt_exit(ser)
                break
            elif choice == "11":
                opt_connect(ser)
            elif choice == "12":
                opt_cfg_exit(ser)
            elif choice == "13":
                opt_logread_range(ser)
            else:
                print("Opción inválida.")

    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import serial
import json
import os
import sys
from datetime import datetime

# ==========================================================
# CONFIGURACION
# ==========================================================

SERIAL_PORT = "/dev/ttyACM0"
BAUDRATE = 9600
JSON_FILE = "/home/pi/config_modem.json"

TIMEOUT = 2.5

# ==========================================================
# CRC16 CCITT
# ==========================================================

def crc16_ccitt(data: bytes) -> int:

    crc = 0xFFFF

    for byte in data:

        crc ^= (byte << 8)

        for _ in range(8):

            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF

    return crc


# ==========================================================
# CRC32
# ==========================================================

def crc32_custom(data: bytes) -> int:

    crc = 0xFFFFFFFF

    for byte in data:

        crc ^= byte

        for _ in range(8):

            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1

    return (~crc) & 0xFFFFFFFF


# ==========================================================
# SERIAL
# ==========================================================

def open_serial():

    try:

        ser = serial.Serial(
            port=SERIAL_PORT,
            baudrate=BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=TIMEOUT
        )

        return ser

    except Exception as e:

        print(f"\nERROR abriendo UART: {e}\n")
        return None


# ==========================================================
# ENVIO DE TRAMA
# ==========================================================

def send_frame(ser, frame_without_crc):

    crc = crc16_ccitt(frame_without_crc.encode())

    frame = f"{frame_without_crc}*{crc:04X}"

    print("\n===================================")
    print("TRAMA TRANSMITIDA")
    print("===================================")
    print(frame)
    print("===================================\n")

    ser.write(frame.encode())

    return frame


# ==========================================================
# RECEPCION
# ==========================================================

def receive_response(ser):

    try:

        data = ser.readline()

        if len(data) == 0:
            return None

        return data.decode(errors="ignore").strip()

    except Exception as e:

        print("Error recepción:", e)
        return None


# ==========================================================
# INTERPRETAR RESPUESTAS
# ==========================================================

def decode_response(resp):

    if resp is None:
        print("\nTIMEOUT\n")
        return

    print("\n===================================")
    print("RESPUESTA MODEM")
    print("===================================")
    print(resp)
    print("===================================\n")

    if "ACK,READ,OK" in resp:
        print("Lectura aceptada")

    elif "ACK,WRITE,OK" in resp:
        print("Configuración recibida")

    elif "ACK,SAVE,OK" in resp:
        print("Configuración guardada en FLASH")

    elif "ACK,DEFAULT,OK" in resp:
        print("Valores de fábrica restaurados")

    elif "ACK,REBOOT,OK" in resp:
        print("Reinicio solicitado")

    elif "ERROR,PASSWORD" in resp:
        print("Password incorrecta")

    elif "ERROR,CRC" in resp:
        print("CRC incorrecto")

    elif "ERROR,JSON" in resp:
        print("JSON inválido")

    elif "ERROR,FLASH" in resp:
        print("Error FLASH")

    elif "ERROR,MODE" in resp:
        print("Modo incorrecto")

    elif "ERROR,FORMAT" in resp:
        print("Formato inválido")

    elif "ERROR,TIMEOUT" in resp:
        print("Timeout modem")

    elif "ERROR,UNKNOWN" in resp:
        print("Error genérico")


# ==========================================================
# ACTUALIZAR TIMESTAMP
# ==========================================================

def update_timestamp():

    if not os.path.exists(JSON_FILE):

        print("No existe:")
        print(JSON_FILE)
        return None

    with open(JSON_FILE, "r") as f:
        cfg = json.load(f)

    cfg["config_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    with open(JSON_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

    return cfg


# ==========================================================
# OPCION 1
# ==========================================================

def read_configuration():

    password = input("Password configuración: ")

    ser = open_serial()

    if ser is None:
        return

    frame = f"$CFG,READ,{password}"

    send_frame(ser, frame)

    response = receive_response(ser)

    if response:

        if response.startswith("$CFG,DATA,"):

            try:

                first = response.find("{")
                last = response.rfind("}")

                json_text = response[first:last+1]

                cfg = json.loads(json_text)

                with open(JSON_FILE, "w") as f:
                    json.dump(cfg, f, indent=4)

                print("\nConfiguración guardada en:")
                print(JSON_FILE)

            except Exception as e:

                print("Error parseando JSON:", e)

        else:
            decode_response(response)

    ser.close()


# ==========================================================
# OPCION 2
# ==========================================================

def write_configuration():

    cfg = update_timestamp()

    if cfg is None:
        return

    password = input("Password configuración: ")

    json_text = json.dumps(cfg, separators=(',', ':'))

    json_length = len(json_text)

    print("\n===================================")
    print("JSON A ENVIAR")
    print("===================================")
    print(json.dumps(cfg, indent=4))
    print("===================================\n")

    frame = (
        f"$CFG,WRITE,"
        f"{password},"
        f"{json_length},"
        f"{json_text}"
    )

    ser = open_serial()

    if ser is None:
        return

    send_frame(ser, frame)

    response = receive_response(ser)

    decode_response(response)

    ser.close()


# ==========================================================
# OPCION 3
# ==========================================================

def save_flash():

    password = input("Password configuración: ")

    ser = open_serial()

    if ser is None:
        return

    frame = f"$CFG,SAVE,{password}"

    send_frame(ser, frame)

    response = receive_response(ser)

    decode_response(response)

    ser.close()


# ==========================================================
# OPCION 4
# ==========================================================

def restore_defaults():

    password = input("Password configuración: ")

    ser = open_serial()

    if ser is None:
        return

    frame = f"$CFG,DEFAULT,{password}"

    send_frame(ser, frame)

    response = receive_response(ser)

    decode_response(response)

    ser.close()


# ==========================================================
# OPCION 5
# ==========================================================

def reboot_modem():

    password = input("Password configuración: ")

    ser = open_serial()

    if ser is None:
        return

    frame = f"$CFG,REBOOT,{password}"

    send_frame(ser, frame)

    response = receive_response(ser)

    decode_response(response)

    ser.close()


# ==========================================================
# MENU
# ==========================================================

def menu():

    while True:

        print("\n")
        print("===================================")
        print(" CONFIGURADOR MODEM A7670 ")
        print("===================================")
        print("1 - Leer configuración")
        print("2 - Escribir configuración")
        print("3 - Guardar FLASH")
        print("4 - Restaurar fábrica")
        print("5 - Reiniciar módem")
        print("6 - Salir")
        print("===================================")

        option = input("Seleccione: ")

        if option == "1":
            read_configuration()

        elif option == "2":
            write_configuration()

        elif option == "3":
            save_flash()

        elif option == "4":
            restore_defaults()

        elif option == "5":
            reboot_modem()

        elif option == "6":
            print("\nSaliendo...\n")
            sys.exit(0)

        else:
            print("\nOpción inválida\n")


# ==========================================================
# MAIN
# ==========================================================

if __name__ == "__main__":

    menu()


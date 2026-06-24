"""
===============================================================================
SIMULADOR DE CENTRAL DE ABONADO - PROTOCOLO RS-485 (V1.0)
===============================================================================
Este script emula el comportamiento del Master (Central de Abonado) para realizar 
pruebas de integración con la Placa Expansora basada en Raspberry Pi Pico (RP2040).

FUNCIONALIDAD PRINCIPAL:
1. Interrogación Cíclica (Polling): Envía comandos 'P' para solicitar eventos.
2. Control de Relés: Permite activar las salidas de potencia mediante bitmask.
3. Decodificación Contact ID: Parsea las tramas de 26 bytes enviadas por la 
   expansora, extrayendo el estado de las zonas y eventos de alarma.

PARÁMETROS DE COMUNICACIÓN (Críticos para consistencia con firmware):
- Baudrate: 9600 bps
- Paridad: Par (Even) - Definido en hardware/uart.h de la placa.
- Bits de Parada: 2 - Requerido por la configuración UART_PARITY_EVEN de la Pico.
- Control de Flujo: Ninguno (Half-Duplex sobre RS-485).

ESTRUCTURA DE TRAMA (Prot485):
[LF (0x0A)] [RTU_ADDR] [CMD] [PAYLOAD...] [CHECKSUM] [CR (0x0D)]

Nota: El Checksum es la suma de todos los bytes (excluyendo el LF inicial) 
módulo 256. La respuesta esperada de la placa es de 26 bytes fijos.

Desarrollado para validación de gateways GPRS/Ethernet industriales.
===============================================================================
"""

import serial
import time

# ================= CONFIGURACION DE HARDWARE =================
PORT = '/dev/ttyUSB0'  # Ajusta según tu sistema (COMx en Windows)
BAUDRATE = 9600
RTU_ADDRESS = 230      # Dirección definida en tu includes.h (MYRTUADDRESS)

# Configuración estricta según tu firmware: 9600,E,8,2
ser = serial.Serial(
    port=PORT,
    baudrate=BAUDRATE,
    parity=serial.PARITY_EVEN,    # PARIDAD PAR (E)
    stopbits=serial.STOPBITS_TWO, # 2 BITS DE PARADA
    bytesize=serial.EIGHTBITS,
    timeout=2.5
)

def calc_checksum(data):
    """Calcula el checksum sumando los bytes (sin el LF inicial) % 256"""
    return sum(data) % 256

def build_frame(addr, cmd, payload=None):
    if payload is None: payload = []
    frame = bytearray()
    frame.append(0x0A)         # LF (Inicio)
    frame.append(addr)         # RTU Address
    frame.append(ord(cmd))     # Comando ('P', 'R', etc)
    for b in payload:
        frame.append(b)
    
    # El checksum se calcula sobre la dirección, comando y datos
    checksum = calc_checksum(frame[1:])
    frame.append(checksum)
    frame.append(0x0D)         # CR (Fin)
    return frame

def send_command(cmd_char, payload=None):
    trama = build_frame(RTU_ADDRESS, cmd_char, payload)
    ser.write(trama)
    # Tu placa expansora responde con 26 bytes según el parser de la central
    response = ser.read(26) 
    return response

def parse_zones(response):
    """Extrae información del evento CID si existe en la trama"""
    if len(response) >= 20 and b'[' in response:
        try:
            # Buscamos la estructura [ACCT Q EEE PP ZZZ]
            msg = response.decode('ascii', errors='ignore')
            start = msg.find('[')
            end = msg.find(']')
            if start != -1 and end != -1:
                cid_data = msg[start+1:end]
                return f"EVENTO CID: {cid_data}"
        except:
            pass
    return response.hex().upper()

def main():
    print(f"--- Iniciando Comunicación con Placa Expansora (ID: {RTU_ADDRESS}) ---")
    
    try:
        while True:
            print("\nAcciones: [1] Polling (Zonas) | [2] Activar Relé | [3] Salir")
            opcion = input("Seleccione una opción: ")

            if opcion == '1':
                # 'P' de Polling + Password (por defecto 0x01 segun tus fuentes)
                resp = send_command('P', [0x30, 0x30, 0x31]) 
                if resp:
                    print(f"Respuesta Expansora: {parse_zones(resp)}")
                else:
                    print("ERROR: La placa no responde (Timeout)")

            elif opcion == '2':
                rele = int(input("Número de Relé (1-8): "))
                # Comando 'R' según Prot485.md
                # Byte 1: Estado (Bitmask), Byte 2: Pulso (Bitmask)
                bitmask = 1 << (rele - 1)
                print(f"Enviando pulso a Relé {rele}...")
                resp = send_command('R', [0x00, bitmask]) 
                print("Comando enviado.")

            elif opcion == '3':
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nCerrando...")
    finally:
        ser.close()

if __name__ == "__main__":
    main()

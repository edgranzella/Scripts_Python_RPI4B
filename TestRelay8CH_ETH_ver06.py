"""
===============================================================================
SIMULADOR DE CENTRAL DE ABONADO - PROTOCOLO RS-485 (V1.0)
===============================================================================
Ajustado estrictamente al protocolo documentado del PLC:
1. Comandos Master -> PLC: SIN Checksum.
2. Longitudes de trama fijas según comando.
3. Validación de Checksum en respuestas del PLC (26 bytes).
===============================================================================
"""

import serial
import time

# ================= CONFIGURACION DE HARDWARE =================
PORT = '/dev/ttyUSB0'  # Ajusta según tu sistema (COMx en Windows)
BAUDRATE = 9600
RTU_ADDRESS = 230      # Dirección de la placa PLC

# Configuración 9600,E,8,2
ser = serial.Serial(
    port=PORT,
    baudrate=BAUDRATE,
    parity=serial.PARITY_EVEN,
    stopbits=serial.STOPBITS_TWO,
    bytesize=serial.EIGHTBITS,
    timeout=2.5
)

def validate_checksum(response):
    """
    Verifica el checksum de la respuesta enviada por la Pico.
    Suma desde byte[1] hasta byte[23] y compara con byte[24].
    """
    if len(response) < 26:
        return False
    
    # El protocolo dice: suma de rxbuffer[1] a rxbuffer[23]
    calc_cksum = sum(response[1:24]) % 256
    sent_cksum = response[24]
    
    return calc_cksum == sent_cksum

def build_frame_protocol(addr, cmd, payload):
    """
    Construye la trama según el Protocolo Documentado (SIN CHECKSUM de salida).
    """
    frame = bytearray()
    frame.append(0x0A)         # LF
    frame.append(addr)         # RTU Address
    frame.append(ord(cmd))     # Comando ('P', 'R', 'A', '@')
    
    for b in payload:
        frame.append(b)
    
    frame.append(0x0D)         # CR
    return frame

def send_command(cmd_char, payload=None):
    if payload is None: payload = []
    
    trama = build_frame_protocol(RTU_ADDRESS, cmd_char, payload)
    
    # Limpiar buffers antes de enviar
    ser.reset_input_buffer()
    
    ser.write(trama)
    print(f"TX -> {[hex(b) for b in trama]}")
    
    # La placa responde con 26 bytes según el protocolo
    response = ser.read(26) 
    return response

def parse_response(response):
    if not response:
        return "ERROR: Timeout - La placa no responde"
    
    if len(response) != 26:
        return f"ERROR: Trama incompleta ({len(response)} bytes)"

    if not validate_checksum(response):
        return f"ERROR: Checksum inválido en respuesta (Esperado: {hex(response[24])})"

    try:
        # Decodificación del cuerpo de la trama (bytes 1 al 23)
        msg = response.decode('ascii', errors='ignore')
        
        # Si contiene '[', es un evento Contact ID
        if '[' in msg:
            start = msg.find('[')
            end = msg.find(']')
            return f"EVENTO CID RECIBIDO: {msg[start:end+1]}"
        
        # Si no, es un paquete de Status (byte 4 es 0x5A o 0x5B)
        status_type = response[4]
        if status_type == 0x5A:
            return f"STATUS RECIBIDO (Byte 3: {bin(response[3])})"
        elif status_type == 0x5B:
            return f"VERSION RECIBIDA: {response[3]}"
            
    except Exception as e:
        return f"Error parseando: {e}"

    return response.hex().upper()

def main():
    print(f"--- Master Central (ID: {RTU_ADDRESS}) ---")
    print(f"Protocolo: {BAUDRATE},E,8,2")
    
    try:
        while True:
            print("\nAcciones: [1] Poll 'P' | [2] Relés 'R' | [3] Salir")
            opcion = input("Seleccione: ")

            if opcion == '1':
                # Protocolo 'P': 1 byte de password (ej: dígito '1')
                # Trama final: [0x0A][230]['P'][0x31][0x0D] (5 bytes)
                resp = send_command('P', [0x31]) 
                print(f"RX <- {parse_response(resp)}")

            elif opcion == '2':
                rele = int(input("Número de Relé (1-8): "))
                # Protocolo 'R': [0x0A][230]['R'][Estado][Pulso][0x0D] (6 bytes)
                bitmask = 1 << (rele - 1)
                # Ejemplo: Encender (Estado bitmask) y Pulso (0)
                resp = send_command('R', [bitmask, 0x00]) 
                print(f"RX <- {parse_response(resp)}")

            elif opcion == '3':
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nCerrando...")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
    

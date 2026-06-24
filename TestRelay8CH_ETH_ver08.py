
"""
===============================================================================
SIMULADOR DE CENTRAL DE ABONADO - PROTOCOLO RS-485 (V1.0)
===============================================================================
Sincronizado con la lógica de la Placa PLC (ID: 230):
1. Envío: Calcula Checksum según el protocolo de la placa.
2. Recepción: Valida Checksum de la respuesta de 26 bytes.
===============================================================================
"""

import serial
import time

# ================= CONFIGURACION DE HARDWARE =================
PORT = '/dev/ttyUSB0'  # Cambiar según corresponda (ej: 'COM3' o '/dev/ttyUSB0')
BAUDRATE = 9600
RTU_ADDRESS = 230      # Dirección fija de la placa PLC

# Configuración 9600,E,8,2 (Importante para RP2040)
ser = serial.Serial(
    port=PORT,
    baudrate=BAUDRATE,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    bytesize=serial.EIGHTBITS,
    timeout=5.0
)

def calculate_checksum(data):
    """
    Calcula el Checksum igual que la Placa PLC.
    Suma de los bytes desde el índice 1 (excluye el LF inicial).
    """
    # Suma todos los bytes excepto el primero (0x0A)
    # El módulo 256 es equivalente al comportamiento de un uint8_t en C
    return sum(data[1:]) % 256

def build_frame(addr, cmd, payload):
    """
    Construye la trama: [LF][ADDR][CMD][DATA...][CSUM][CR]
    """
    frame = bytearray()
    frame.append(0x0A)         # LF (Inicio de trama)
    frame.append(addr)         # Dirección (230)
    frame.append(ord(cmd))     # Comando ASCII
    
    for b in payload:
        frame.append(b)
       
    # Calculamos el checksum antes de cerrar la trama
    # csum = calculate_checksum(frame)
    # frame.append(csum)
    
    frame.append(0x0D)         # CR (Fin de trama)
    return frame

def send_command(cmd_char, payload=None):
    if payload is None: payload = []
    
    trama = build_frame(RTU_ADDRESS, cmd_char, payload)
    ser.reset_input_buffer()    # Limpiar ruidos previos   
    ser.write(trama)
    
    # print(f"TX -> {[hex(b) for b in trama]}")
    print(f"TX -> {trama.hex().upper()}")

    time.sleep(1) # Dale un respiro a la Pico para procesar

    if ser.in_waiting > 0:
        response = ser.read(ser.in_waiting)
        print(f"RX BRUTO -> {response.hex().upper()} (Len: {len(response)})")
    else:
        print("RX -> NADA (Silencio total)")

    # Según protocolo, la respuesta es de 26 bytes
    # response = ser.read(26) 
    # response = ser.read(ser.in_waiting or 1)
    # print(f"DEBUG RX -> {response.hex().upper()}")

    return response

def validate_response(response):
    """
    Valida la respuesta de la placa usando la misma lógica de Checksum.
    """
    if len(response) != 26:
        return f"ERROR: Longitud incorrecta ({len(response)} bytes)"
    
    # Checksum esperado está en el byte 24
    # Calculamos sobre los bytes 1 al 23
    calc_csum = sum(response[1:24]) % 256
    recv_csum = response[24]
    
    if calc_csum != recv_csum:
        return f"ERROR: Checksum no coincide (Calc: {hex(calc_csum)}, Recv: {hex(recv_csum)})"
    
    return "OK"

def main():
    print(f"--- Master Central (ID: {RTU_ADDRESS}) ---")
    
    try:
        while True:
            print("\n[1] Polling 'P' | [2] Relés 'R' | [3] ReléPulse 'R' | [4] Salir | [5] Recepción Pura")
            opcion = input("Seleccione: ")

            if opcion == '1':
                # Polling 'P' con un dígito de password (ej: '1')
                resp = send_command('P', [0x31]) 
                status = validate_response(resp)
                if status == "OK":
                    print(f"RX <- {resp.hex().upper()}")
                else:
                    print(status)

            elif opcion == '2':
                rele = int(input("Relé (1-8): "))
                # 'R' [Estado][Pulso]

                if rele == 0:
                    bitmask = 0
                else:
                    bitmask = 1 << (rele - 1)
                    
                resp = send_command('R', [bitmask, 0x00])
                status = validate_response(resp)
                if status == "OK":
                    print("Comando de relé aceptado por la placa.")
                else:
                    print(status)

            elif opcion == '3':
                rele = int(input("Relé (1-8): "))
                # 'R' [Estado][Pulso]

                if rele == 0:
                    bitmask = 0
                else:
                    bitmask = 1 << (rele - 1)

                resp = send_command('R', [0x00, bitmask])
                status = validate_response(resp)
                if status == "OK":
                    print("Comando de relé aceptado por la placa.")
                else:
                    print(status)

            elif opcion == '4':
                break

# --- NUEVA OPCIÓN 5: RECEPCIÓN PURA ---
            elif opcion == '5':
                print("\n--- Entrando en modo Recepción Pura ---")
                print("Escuchando el bus RS-485... Presione Ctrl+C para salir al menú.\n")
                
                ser.reset_input_buffer() # Limpiamos basura previa del buffer
                en_trama = False
                linea_trama = ""
                tiempo_anterior = None # Inicializamos la variable de tiempo

                try:
                    while True:
                        if ser.in_waiting > 0:
                            # Leer de a un byte para procesar la máquina de estados en Python
                            byte_in = ser.read(1)[0] 

                            # Detectar inicio de trama (LF)
                            if byte_in == 0x0A:
                                en_trama = True
                                linea_trama = "[0x0A]" # Iniciamos la línea con el delimitador claro
                                continue # Pasamos al siguiente byte
                            
                            # Si estamos dentro de una trama válida, procesamos el contenido
                            if en_trama:
                                if byte_in == 0x0D:
                                    # Detectar fin de trama (CR)
                                    linea_trama += "[0x0D]"
                                    print(linea_trama) # Imprime la trama completa en una sola línea
                                    
                                    # --- CÁLCULO DE TIMESTAMP ENTRE TRAMAS ---
                                    tiempo_actual = time.perf_counter()
                                    if tiempo_anterior is not None:
                                        # Multiplicamos por 1000 para convertir segundos a milisegundos
                                        delta_ms = (tiempo_actual - tiempo_anterior) * 1000
                                        print(f" -> Tiempo desde la trama anterior: {delta_ms:.2f} ms")
                                    else:
                                        print(" -> Primera trama recibida (referencia inicial)")
                                    
                                    tiempo_anterior = tiempo_actual # Actualizamos el marcador
                                    # ------------------------------------------

                                    en_trama = False   # Esperar a la siguiente trama
                                else:
                                    # Verificar si es un carácter ASCII imprimible (entre el espacio y la tilde)
                                    if 32 <= byte_in <= 126:
                                        linea_trama += chr(byte_in)
                                    else:
                                        # Si es binario/control, lo formateamos a Hexadecimal de 2 dígitos
                                        linea_trama += f"[{byte_in:02X}]"
                        else:
                            time.sleep(0.001) # Evitar que el hilo de Python sature la CPU al 100%

                except KeyboardInterrupt:
                    print("\n--- Modo Recepción Pura Finalizado. Volviendo al menú. ---")

    except KeyboardInterrupt:
        print("\nFinalizado.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()


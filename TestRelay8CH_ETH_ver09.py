
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

# Estructura global para almacenar métricas de los dispositivos
# Guardamos los tiempos de respuesta en una lista por ID para calcular promedio, min y max
# Estructura global para almacenar métricas de los dispositivos
estadisticas_bus = {
    "0B": {"nombre": "PT1", "tiempos": [], "ticks": 0, "tiempo_interrogacion": None},
    "15": {"nombre": "PT2", "tiempos": [], "ticks": 0, "tiempo_interrogacion": None},
    "E6": {"nombre": "Placa Rele", "tiempos": [], "ticks": 0, "tiempo_interrogacion": None}
}

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
            print("\n[1] Polling 'P' | [2] Relés 'R' | [3] ReléPulse 'R' | [4] Salir | [5] Recepción Pura | [6] Estadísticas")
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
                
                ser.reset_input_buffer() 
                en_trama = False
                linea_trama = ""
                bytes_crudos = bytearray() 

                try:
                    while True:
                        if ser.in_waiting > 0:
                            byte_in = ser.read(1)[0] # Leemos el byte directamente como entero

                            if byte_in == 0x0A:
                                en_trama = True
                                linea_trama = "[0x0A]" 
                                bytes_crudos = bytearray([0x0A]) 
                                continue 
                            
                            if en_trama:
                                bytes_crudos.append(byte_in)
                                if byte_in == 0x0D:
                                    linea_trama += "[0x0D]"
                                    print(linea_trama) 
                                    
                                    # --- PROCESAMIENTO DE PROTOCOLO ---
                                    if len(bytes_crudos) >= 3:
                                        # Caso 1: RESPUESTA DEL DISPOSITIVO ([0x0A], 'A', ID, ...)
                                        # 'A' en ASCII es 0x41 (65 en decimal)
                                        if bytes_crudos[1] == 0x41: 
                                            id_respuesta = f"{bytes_crudos[2]:02X}"
                                            
                                            if id_respuesta in estadisticas_bus:
                                                info = estadisticas_bus[id_respuesta]
                                                # Verificamos si teníamos una interrogación previa registrada
                                                if info["tiempo_interrogacion"] is not None:
                                                    tiempo_respuesta = time.perf_counter()
                                                    delta_ms = (tiempo_respuesta - info["tiempo_interrogacion"]) * 1000
                                                    
                                                    # Guardamos las métricas
                                                    info["tiempos"].append(delta_ms)
                                                    info["ticks"] += 1
                                                    print(f" -> [RESPUESTA 0x{id_respuesta}] Tiempo de respuesta: {delta_ms:.2f} ms")
                                                    
                                                    # Limpiamos el flag para esperar la siguiente interrogación
                                                    info["tiempo_interrogacion"] = None
                                                else:
                                                    print(f" -> [RESPUESTA 0x{id_respuesta}] Huérfana (sin interrogación previa)")

                                        # Caso 2: INTERROGACIÓN DE LA CENTRAL ([0x0A], ID, ...)
                                        else:
                                            id_interrogacion = f"{bytes_crudos[1]:02X}"
                                            if id_interrogacion in estadisticas_bus:
                                                # Guardamos el momento exacto en que termina de llegar la interrogación
                                                estadisticas_bus[id_interrogacion]["tiempo_interrogacion"] = time.perf_counter()
                                                print(f" -> [INTERROGACIÓN] Central busca a 0x{id_interrogacion}")

                                    en_trama = False   
                                else:
                                    if 32 <= byte_in <= 126:
                                        linea_trama += chr(byte_in)
                                    else:
                                        linea_trama += f"[{byte_in:02X}]"
                        else:
                            time.sleep(0.001)
                except KeyboardInterrupt:
                    print("\n--- Salida del modo Recepción Pura ---")
# --- NUEVA OPCIÓN 6: ESTADÍSTICAS DEL BUS ---
            elif opcion == '6':
                print("\n========================================================")
                print("         ESTADÍSTICAS DE RESPUESTA DE DISPOSITIVOS       ")
                print("========================================================\n")
                
                MARGEN_TOLERANCIA = 0.10 

                for id_dev, data in estadisticas_bus.items():
                    print(f"Dispositivo: {data['nombre']} (ID: 0x{id_dev})")
                    print(f"--------------------------------------------------------")
                    
                    respuestas_exitosas = data["ticks"]
                    print(f" Respuestas completas registradas: {respuestas_exitosas}")
                    
                    if respuestas_exitosas == 0:
                        print(" No se registraron ciclos de respuesta para este dispositivo todavía.\n")
                        continue
                    
                    tiempos = data["tiempos"]
                    t_min = min(tiempos)
                    t_max = max(tiempos)
                    t_prom = sum(tiempos) / len(tiempos)
                    
                    rango_total = t_max - t_min if (t_max - t_min) > 0 else 1
                    limite_cerca_min = t_min + (rango_total * MARGEN_TOLERANCIA)
                    limite_cerca_max = t_max - (rango_total * MARGEN_TOLERANCIA)
                    
                    cerca_min_cant = sum(1 for t in tiempos if t <= limite_cerca_min)
                    cerca_max_cant = sum(1 for t in tiempos if t >= limite_cerca_max)
                    
                    pct_cerca_min = (cerca_min_cant / respuestas_exitosas) * 100
                    pct_cerca_max = (cerca_max_cant / respuestas_exitosas) * 100

                    print(f" Tiempo Mínimo de Respuesta:   {t_min:.2f} ms")
                    print(f" Tiempo Máximo de Respuesta:   {t_max:.2f} ms")
                    print(f" Tiempo Promedio de Respuesta: {t_prom:.2f} ms")
                    print(f" Cerca del Mínimo (<={limite_cerca_min:.2f} ms): {cerca_min_cant} veces ({pct_cerca_min:.1f}%)")
                    print(f" Cerca del Máximo (>={limite_cerca_max:.2f} ms): {cerca_max_cant} veces ({pct_cerca_max:.1f}%)")
                    print("\n")
                
                print("========================================================")
                input("Presione Enter para volver al menú principal...")

    except KeyboardInterrupt:
        print("\nFinalizado.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()


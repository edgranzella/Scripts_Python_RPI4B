import serial
import time

# Configuración del puerto
PORT = '/dev/ttyUSB2'
BAUD = 115200
MAX_REINTENTOS = 3

def send_at(ser, command, wait=1, back="OK"):
    ser.write((command + '\r\n').encode())
    time.sleep(wait)
    if ser.in_waiting > 0:
        res = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
        return res if back in res else None
    return None

try:
    ser = serial.Serial(PORT, BAUD, timeout=1)
    print(f"--- Iniciando TCP con Reintentos en {PORT} ---")

    # 1. Preparar Red
    send_at(ser, 'AT+CGDCONT=1,"IP","internet.movistar.arg"')
    send_at(ser, 'AT+NETOPEN')
    send_at(ser, 'AT+CIPRXGET=1') # Modo manual de recepción

    # 2. Intentar conexión al Socket
    # if send_at(ser, 'AT+CIPOPEN=0,"TCP","45.79.112.203",4242', wait=3, back="+CIPOPEN: 0,0"):
    if send_at(ser, 'AT+CIPOPEN=0,"TCP","190.111.217.188",57777', wait=3, back="+CIPOPEN: 0,0"):
        exito = False
        for intento in range(1, MAX_REINTENTOS + 1):
            print(f"\n[Intento {intento}] Enviando trama...")
            
            # Limpiar buffer de entrada antes de enviar
            ser.read(ser.in_waiting)
            
            msg = f"DATA_PRUEBA_{intento}"
            ser.write(f'AT+CIPSEND=0,{len(msg)}\r\n'.encode())
            time.sleep(0.5)
            ser.write(msg.encode())
            
            # 3. Espera inteligente del aviso +CIPRXGET: 1 (hasta 5 seg)
            timeout = time.time() + 5
            while time.time() < timeout:
                if ser.in_waiting > 0:
                    linea = ser.readline().decode('utf-8', errors='ignore')
                    if "+CIPRXGET: 1" in linea:
                        print(">> ¡Datos recibidos en el módulo!")
                        # 4. Leer los datos
                        respuesta = send_at(ser, 'AT+CIPRXGET=2,0,50')
                        print(f">> RESPUESTA SERVIDOR: {respuesta.strip()}")
                        exito = True
                        break
                time.sleep(0.2)
            
            if exito: break
            print(">> Sin respuesta del servidor, reintentando...")

        if not exito:
            print("\nError: Agotados los reintentos sin respuesta del Eco.")

        send_at(ser, 'AT+CIPCLOSE=0')
    
    send_at(ser, 'AT+NETCLOSE')
    ser.close()

except Exception as e:
    print(f"Error crítico: {e}")

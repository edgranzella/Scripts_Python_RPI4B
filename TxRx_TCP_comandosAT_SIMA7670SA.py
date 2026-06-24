import serial
import time

# Configuración del puerto (Asegúrate de que wvdial esté detenido)
PORT = '/dev/ttyUSB2'
BAUD = 115200

def send_at(ser, command, wait=1, back="OK"):
    """Envía un comando AT y espera una respuesta específica."""
    ser.write((command + '\r\n').encode())
    time.sleep(wait)
    res = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    print(f"Comando: {command}\nRespuesta:\n{res}")
    return res if back in res else None

try:
    # 1. Abrir puerto serial
    ser = serial.Serial(PORT, BAUD, timeout=2)
    print(f"--- Iniciando comunicación TCP directa en {PORT} ---")

    # 2. Configurar el contexto de red (Movistar)
    send_at(ser, 'AT+CGDCONT=1,"IP","internet.movistar.arg"')
    
    # 3. Abrir stack TCP y verificar IP
    if send_at(ser, 'AT+NETOPEN'):
        send_at(ser, 'AT+CGPADDR=1')
        
        # 4. Configurar recepción de datos manual (más fácil para el script)
        send_at(ser, 'AT+CIPRXGET=1')

        # 5. Abrir Socket TCP (Usando el servidor de ECO tcpbin.com)
        # 45.79.112.203 es la IP de tcpbin.com
        if send_at(ser, 'AT+CIPOPEN=0,"TCP","45.79.112.203",4242', wait=3, back="+CIPOPEN: 0,0"):
            
            # 6. Enviar trama de datos
            msg = "HOLA_RPi_4G"
            ser.write(f'AT+CIPSEND=0,{len(msg)}\r\n'.encode())
            time.sleep(0.5)
            ser.write(msg.encode()) # Enviamos el mensaje crudo
            time.sleep(1)
            
            # 7. Leer respuesta del servidor (Eco)
            # Primero preguntamos cuánto llegó
            resp = send_at(ser, 'AT+CIPRXGET=2,0,50')
            if "+CIPRXGET:" in resp:
                print("--- TRAMA RECIBIDA CON ÉXITO ---")
            
            # 8. Cerrar conexión
            send_at(ser, 'AT+CIPCLOSE=0')
    
    send_at(ser, 'AT+NETCLOSE')
    ser.close()

except Exception as e:
    print(f"Error en el script: {e}")

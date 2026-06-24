import serial
import time

# Configuración del puerto
PORT = '/dev/ttyUSB2'
BAUD = 115200

def run_subscriber():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
        print(f"--- Escuchando en {PORT} (Tópico: casa/prueba) ---")
        
        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                
                # Detectamos cuando el módulo avisa que llegó un mensaje
                if "+CMQTTRXPAYLOAD" in line:
                    # El formato es +CMQTTRXPAYLOAD: <id>,<largo>
                    # Leemos la siguiente línea que contiene el mensaje real
                    msg = ser.readline().decode('utf-8', errors='ignore').strip()
                    print(f" [NUEVO DATO] >> {msg}")
            time.sleep(0.1)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_subscriber()

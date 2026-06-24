import serial

# Configura el puerto de tu módulo
ser = serial.Serial('/dev/ttyUSB2', 115200, timeout=1)

print("Escuchando mensajes MQTT en casa/prueba...")
while True:
    line = ser.readline().decode('utf-8', errors='ignore')
    if "+CMQTTRXPAYLOAD" in line:
        # La siguiente línea después del aviso es el mensaje real
        mensaje = ser.readline().decode('utf-8').strip()
        print(f"--> MENSAJE RECIBIDO: {mensaje}")

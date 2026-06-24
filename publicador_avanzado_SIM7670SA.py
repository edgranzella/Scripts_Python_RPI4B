import serial
import time
import psutil
import json

ser = serial.Serial('/dev/ttyUSB2', 115200, timeout=1)

def enviar_comando(cmd, espera=0.5):
    ser.write((cmd + '\r\n').encode())
    time.sleep(espera)

def publicar_telemetria():
    # 1. Obtener datos reales de la Raspberry Pi 4B
    cpu_uso = psutil.cpu_percent()
    memoria = psutil.virtual_memory().available >> 20  # Convertir a MB
    
    # Crear un string corto para no exceder buffers
    data = {"cpu": cpu_uso, "mem_free_mb": memoria}
    msg = json.dumps(data)
    
    print(f"Enviando telemetría: {msg}")

    # 2. Secuencia de comandos AT MQTT
    enviar_comando('AT+CMQTTTOPIC=0,11')
    enviar_comando('casa/prueba')
    
    enviar_comando(f'AT+CMQTTPAYLOAD=0,{len(msg)}')
    enviar_comando(msg)
    
    enviar_comando('AT+CMQTTPUB=0,1,60')

print("--- Iniciando Publicador Automático ---")
try:
    while True:
        publicar_telemetria()
        time.sleep(10) # Envía datos cada 10 segundos
except KeyboardInterrupt:
    print("Publicador detenido.")
    ser.close()

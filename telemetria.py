
import paho.mqtt.client as mqtt
import time
import os
import subprocess

def get_ppp_ip():
    # Obtiene la IP actual de ppp0 de forma dinámica
    try:
        cmd = "ip -4 addr show ppp0 | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}'"
        ip = subprocess.check_output(cmd, shell=True).decode().strip()
        return ip
    except:
        return None

def get_temp():
    res = os.popen('vcgencmd measure_temp').readline()
    return res.replace("temp=","").replace("'C\n","")

# 1. Obtenemos la IP de Movistar
ppp_ip = get_ppp_ip()

if ppp_ip:
    print(f"Iniciando MQTT sobre ppp0 con IP: {ppp_ip}")
    # 2. Creamos el cliente especificando la interfaz de salida
    client = mqtt.Client()
    
    try:
        # FORZAMOS el uso de la IP de ppp0 para la conexión
        client.connect("test.mosquitto.org", 1883, bind_address=ppp_ip)
        
        while True:
            temp = get_temp()
            mensaje = f"Temp_RPi4: {temp} C | IP: {ppp_ip}"
            client.publish("casa/vicente_lopez/datos", mensaje)
            print(f"Enviado por 4G: {mensaje}")
            time.sleep(10)
    except Exception as e:
        print(f"Error al conectar por 4G: {e}")
else:
    print("Error: ppp0 no está activa. Verificá wvdial.")

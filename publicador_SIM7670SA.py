import serial
import time

ser = serial.Serial('/dev/ttyUSB2', 115200, timeout=1)

def publicar(msg):
    ser.write(b'AT+CMQTTTOPIC=0,11\r\n')
    time.sleep(0.5)
    ser.write(b'casa/prueba\r\n')
    time.sleep(0.5)
    ser.write(f'AT+CMQTTPAYLOAD=0,{len(msg)}\r\n'.encode())
    time.sleep(0.5)
    ser.write(f'{msg}\r\n'.encode())
    time.sleep(0.5)
    ser.write(b'AT+CMQTTPUB=0,1,60\r\n')
    print(f"Publicado: {msg}")

while True:
    publicar("Dato_RPi_4B")
    time.sleep(5)

import socket

# Configuración
# TCP_IP = 'tcpbin.com'
TCP_IP = '190.111.217.188'  # IP de tcpbin.com
TCP_PORT = 57777
MESSAGE = "HOLA DESDE RPI 4B POR 4G"

# 1. Obtenemos la IP de tu ppp0 (la que vimos antes: 10.36.79.82)
# Podés ponerla a mano para la prueba

INTERFACE_IP = "10.35.168.3" # IP de mi ppp0


try:
    # 2. Creamos el socket TCP
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # 3. VINCULAMOS el socket a la interfaz ppp0 (Movistar)
    s.bind((INTERFACE_IP, 0))

    
    
    # 4. Conectamos y enviamos
    print(f"Conectando a {TCP_IP} por interfaz {INTERFACE_IP}...")
    s.connect((TCP_IP, TCP_PORT))
    s.send(MESSAGE.encode())
    
    # 5. Recibimos el 'Eco'
    data = s.recv(1024)
    s.close()
    
    print(f"Enviado: {MESSAGE}")
    print(f"Recibido: {data.decode()}")

except Exception as e:
    print(f"Error: {e}")

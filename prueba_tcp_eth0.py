import socket

# Configuración del servidor de eco
# TCP_IP = 'tcpbin.com'
# TCP_IP = '45.79.112.203'
# TCP_PORT = 4242

# TCP_IP = '45.79.112.203'
# TCP_PORT = 4242

TCP_IP = '190.111.217.188'
TCP_PORT = 57777

MESSAGE = "HOLA DESDE RPI 4B POR CABLE (ETH0)"

# Tu IP de eth0 según nmcli
INTERFACE_IP = "10.0.41.56" 

try:
    # 1. Crear el socket TCP
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # 2. VINCULAR el socket a la IP de eth0
    # Esto fuerza al sistema a salir por el cable de red
    s.bind((INTERFACE_IP, 0))
    
    # 3. Conectar al servidor
    print(f"Conectando a {TCP_IP} a través de eth0 ({INTERFACE_IP})...")
    s.connect((TCP_IP, TCP_PORT))
    
    # 4. Enviar y recibir
    s.send(MESSAGE.encode())
    data = s.recv(1024)
    s.close()
    
    print(f"Enviado: {MESSAGE}")
    print(f"Recibido del servidor: {data.decode()}")

except Exception as e:
    print(f"Error en la conexión por eth0: {e}")

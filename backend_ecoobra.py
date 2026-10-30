import paho.mqtt.client as mqtt
import json
import csv
import time
import threading
from datetime import datetime

# Configuración
BROKER = "broker.hivemq.com"
TOPIC_WILDCARD = "utp/ds8/equipo_luis/ecoobra/#" # Suscripción con comodín (Req. 6)
TOPIC_TELEMETRIA = "utp/ds8/equipo_luis/ecoobra/telemetria"
TOPIC_ESTADO = "utp/ds8/equipo_luis/ecoobra/estado"

# Variables del Monitor (Req. 8)
estado_actual = "DESCONOCIDO"
ultimo_mensaje_recibido = time.time()
TIEMPO_MAXIMO_SILENCIO = 7.0 # Si no hay datos en 7 seg, pasa a SIN_DATOS

def registrar_evento(tipo, gravedad, detalle):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("eventos.csv", "a", newline='') as f:
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(["fecha", "tipo", "gravedad", "detalle"])
        writer.writerow([fecha, tipo, gravedad, detalle])
    print(f"\n[EVENTO GUARDADO] {fecha} | {tipo} | {gravedad} | {detalle}\n")

def cambiar_estado(nuevo_estado):
    global estado_actual
    if estado_actual != nuevo_estado:
        estado_actual = nuevo_estado
        gravedad = "ALTA" if nuevo_estado in ["OFFLINE", "SIN_DATOS"] else "INFO"
        registrar_evento("CAMBIO_ESTADO", gravedad, f"El dispositivo ahora está {nuevo_estado}")

def monitor_hilo():
    """Hilo secundario que vigila si el dispositivo deja de enviar datos (Req. 8)"""
    while True:
        if estado_actual == "ONLINE" and (time.time() - ultimo_mensaje_recibido) > TIEMPO_MAXIMO_SILENCIO:
            cambiar_estado("SIN_DATOS")
        time.sleep(2)

# Funciones MQTT (API V2)
def on_connect(client, userdata, flags, reason_code, properties):
    print(f"✅ Backend conectado a HiveMQ. Escuchando: {TOPIC_WILDCARD}")
    client.subscribe(TOPIC_WILDCARD)

def on_message(client, userdata, msg):
    global ultimo_mensaje_recibido
    topic = msg.topic
    payload = msg.payload.decode('utf-8')

    # Si es el tema de testamento/estado
    if topic == TOPIC_ESTADO:
        if payload == "ONLINE":
            cambiar_estado("ONLINE")
        elif payload == "OFFLINE":
            cambiar_estado("OFFLINE") # Disparado por el Last Will del broker
        return

    # Si es telemetría
    if topic == TOPIC_TELEMETRIA:
        ultimo_mensaje_recibido = time.time()
        if estado_actual == "SIN_DATOS":
            cambiar_estado("ONLINE") # Se recuperó
            
        try:
            # Validación de datos (Req. 6)
            data = json.loads(payload)
            if "polvo_pm10" in data and "gas_voc" in data:
                if 0 <= data["polvo_pm10"] <= 1000: # Rango lógico
                    # Guardar lectura válida
                    with open("telemetria.csv", "a", newline='') as f:
                        writer = csv.writer(f)
                        if f.tell() == 0:
                            writer.writerow(["fecha", "polvo", "gas", "alarma"])
                        writer.writerow([datetime.now().strftime("%H:%M:%S"), data["polvo_pm10"], data["gas_voc"], data["actuador_buzzer"]])
                    print(f"📊 Datos válidos guardados: Polvo={data['polvo_pm10']} | Gas={data['gas_voc']}")
                else:
                    print("⚠️ Dato descartado: Valor de polvo fuera de rango realista.")
            else:
                print("⚠️ Dato descartado: Faltan campos requeridos en el JSON.")
        except json.JSONDecodeError:
            print("⚠️ Dato descartado: El mensaje no es un JSON válido.")

# Iniciar programa
print("Iniciando Receptor y Monitor EcoObra...")
threading.Thread(target=monitor_hilo, daemon=True).start()

# Configuración API v2 (Req. 6)
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, 1883, 60)
client.loop_forever()
import json
import paho.mqtt.client as mqtt

# ==========================================
# CONFIGURACIÓN DEL BROKER (Debe ser idéntica al ESP32)
# ==========================================
MQTT_BROKER = "broker.hivemq.com"
MQTT_TOPIC = "utp/dsviii/ecoobra/datos"

# ==========================================
# FUNCIONES DE RECEPCIÓN
# ==========================================
def on_connect(client, userdata, flags, rc):
    print(f"✅ Conectado a HiveMQ (Código de estado: {rc})")
    client.subscribe(MQTT_TOPIC)
    print(f"📡 Escuchando datos en vivo del canal: {MQTT_TOPIC}...\n")

def on_message(client, userdata, msg):
    """Esta función se dispara automáticamente cada vez que el ESP32 manda un dato"""
    try:
        # Decodificar el mensaje JSON
        payload_str = msg.payload.decode("utf-8")
        data = json.loads(payload_str)
        
        # Extraer los datos
        polvo = data['sensores_promedio']['polvo_ugm3']
        gas = data['sensores_promedio']['gas_ppm']
        fuente = data['fuente_predominante']
        riesgo = data['nivel_riesgo']
        panico = "🚨 ACTIVADO 🚨" if data['boton_panico'] else "Inactivo"
        
        # Imprimir en la terminal de VS Code
        print("-------------------------------------------------")
        print("🏢 ECOOBRA IOT - REPORTE DE NODO EN VIVO")
        print("-------------------------------------------------")
        print(f"🌫️ Polvo (PM10) : {polvo} ug/m3")
        print(f"🧪 Gas (VOC)    : {gas} ppm")
        print(f"🔍 Detección    : {fuente}")
        print(f"⚠️ Nivel Riesgo : {riesgo}")
        print(f"🆘 Botón Pánico : {panico}")
        print("-------------------------------------------------\n")

    except Exception as e:
        print("Error leyendo el dato entrante:", e)

# ==========================================
# INICIO DEL PROGRAMA
# ==========================================
print("Iniciando Sistema Receptor EcoObra...")
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

# Conectar al broker y mantener el programa escuchando indefinidamente
client.connect(MQTT_BROKER, 1883, 60)
client.loop_forever()
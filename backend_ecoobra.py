import os
import csv
import json
import time
import threading
from datetime import datetime
import paho.mqtt.client as mqtt

# --- CONFIGURACIÓN GENERAL Y UMBRAL DE SILENCIO CONFIGURABLE ---
MQTT_BROKER = os.getenv("ECOOBRA_BROKER", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("ECOOBRA_PORT", "1883"))

# Suscripción con comodín (+) para capturar telemetría y estado de cualquier nodo EcoObra
TOPIC_SUB_TELEMETRIA = "ecoobra/+/telemetria"
TOPIC_SUB_ESTADO     = "ecoobra/+/estado"

# Umbral de silencio configurable (> 3 veces el intervalo de 2 s = 7.0 s por defecto)
UMBRAL_SILENCIO_SEG = float(os.getenv("ECOOBRA_UMBRAL_SILENCIO", "7.0"))

ARCHIVO_TELEMETRIA_CSV = "telemetria_ecoobra.csv"
ARCHIVO_EVENTOS_CSV    = "eventos_ecoobra.csv"

# --- ESTADO DEL MONITOR DE COMUNICACIÓN ---
lock_estado = threading.Lock()
estado_nodos = {}          # {nodo_id: "ONLINE" | "SIN_DATOS" | "OFFLINE"}
ultima_telemetria_ts = {}  # {nodo_id: timestamp_ultima_telemetria_valida}


def inicializar_csvs():
    """Crea los archivos CSV con sus encabezados si no existen."""
    if not os.path.exists(ARCHIVO_TELEMETRIA_CSV):
        with open(ARCHIVO_TELEMETRIA_CSV, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "nodo", "polvo", "gas", "estado_actuador", "nivel", "panico"])

    if not os.path.exists(ARCHIVO_EVENTOS_CSV):
        with open(ARCHIVO_EVENTOS_CSV, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "nodo", "estado_anterior", "estado_nuevo", "motivo"])


def registrar_cambio_estado(nodo, nuevo_estado, motivo):
    """Registra en consola y en eventos_ecoobra.csv cualquier transición de estado."""
    with lock_estado:
        estado_anterior = estado_nodos.get(nodo, "DESCONOCIDO")
        if estado_anterior == nuevo_estado:
            return
        estado_nodos[nodo] = nuevo_estado

    ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[MONITOR] Nodo '{nodo}': {estado_anterior} -> {nuevo_estado} | Motivo: {motivo}")

    try:
        with open(ARCHIVO_EVENTOS_CSV, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([ahora_str, nodo, estado_anterior, nuevo_estado, motivo])
    except Exception as e:
        print("[ERROR CSV EVENTOS] No se pudo guardar el evento:", e)


def es_numero_en_rango(valor, minimo, maximo):
    """Verifica que el valor sea int o float (excluyendo booleanos) y esté dentro del rango."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return False
    val_f = float(valor)
    return minimo <= val_f <= maximo


def validar_telemetria(data):
    """
    Valida rigurosamente tipos y rangos de polvo, gas y estado del actuador
    para evitar errores al procesar datos mal formados.
    """
    if not isinstance(data, dict):
        return False, "El payload JSON no es un objeto/diccionario"

    # 1. Validar Polvo (0.0 a 1000.0 µg/m³)
    if "polvo" not in data:
        return False, "Falta el campo obligatorio 'polvo'"
    if not es_numero_en_rango(data["polvo"], 0.0, 1000.0):
        return False, f"Valor de 'polvo' inválido o fuera de rango (0-1000): {data.get('polvo')}"

    # 2. Validar Gas (0.0 a 500.0 índice/ppm)
    if "gas" not in data:
        return False, "Falta el campo obligatorio 'gas'"
    if not es_numero_en_rango(data["gas"], 0.0, 500.0):
        return False, f"Valor de 'gas' inválido o fuera de rango (0-500): {data.get('gas')}"

    # 3. Validar Estado del Actuador / Nivel
    clave_act = "estado_actuador" if "estado_actuador" in data else ("actuador" if "actuador" in data else None)
    if not clave_act:
        return False, "Falta el campo obligatorio 'estado_actuador'"

    val_act = data[clave_act]
    estados_permitidos = {"ON", "OFF", "ACTIVO", "INACTIVO", "SEGURO", "ALERTA", "CRITICO", "PANICO"}
    if not isinstance(val_act, str) or val_act.upper() not in estados_permitidos:
        return False, f"Valor de '{clave_act}' inválido: {val_act}"

    # 4. Validar campos opcionales si están presentes para prevenir fallos de tipo
    if "panico" in data and not isinstance(data["panico"], bool):
        return False, f"Campo 'panico' debe ser booleano: {data.get('panico')}"

    return True, "OK"


def guardar_telemetria_csv(nodo, data):
    """Guarda una lectura ya validada en el archivo CSV de telemetría."""
    ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    polvo = round(float(data["polvo"]), 2)
    gas = round(float(data["gas"]), 2)
    actuador = data.get("estado_actuador", data.get("actuador", "OFF")).upper()
    nivel = str(data.get("nivel", "SEGURO")).upper()
    panico = bool(data.get("panico", False))

    try:
        with open(ARCHIVO_TELEMETRIA_CSV, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([ahora_str, nodo, polvo, gas, actuador, nivel, panico])
    except Exception as e:
        print("[ERROR CSV TELEMETRÍA] No se pudo escribir la fila:", e)


# --- CALLBACKS PAHO MQTT V2 ---
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"[MQTT] Conectado a {MQTT_BROKER}:{MQTT_PORT} (Umbral silencio: {UMBRAL_SILENCIO_SEG}s)")
        client.subscribe([(TOPIC_SUB_TELEMETRIA, 1), (TOPIC_SUB_ESTADO, 1)])
        print(f"[MQTT] Suscrito con comodines a: '{TOPIC_SUB_TELEMETRIA}' y '{TOPIC_SUB_ESTADO}'")
    else:
        print(f"[MQTT] Error al conectar. Código: {reason_code}")


def on_message(client, userdata, msg):
    partes_tema = msg.topic.split("/")
    nodo_tema = partes_tema[1] if len(partes_tema) >= 3 else "nodo_desconocido"
    tipo_tema = partes_tema[2] if len(partes_tema) >= 3 else partes_tema[-1]

    # 1. Decodificar JSON de forma segura
    try:
        texto = msg.payload.decode("utf-8")
        data = json.loads(texto)
    except Exception:
        print(f"[ERROR JSON] Mensaje mal formado descartado en '{msg.topic}': {msg.payload}")
        return

    # 2. Procesar tema de ESTADO (ONLINE / OFFLINE por Last Will)
    if tipo_tema == "estado":
        estado_msg = None
        if isinstance(data, dict):
            estado_msg = data.get("estado")
        elif isinstance(data, str):
            estado_msg = data

        if estado_msg in ("ONLINE", "OFFLINE"):
            motivo = "Mensaje de estado / Last Will recibido" if estado_msg == "OFFLINE" else "Conexión reportada por el nodo"
            registrar_cambio_estado(nodo_tema, estado_msg, motivo)
            if estado_msg == "ONLINE":
                with lock_estado:
                    ultima_telemetria_ts[nodo_tema] = time.time()
        else:
            print(f"[AVISO ESTADO] Estado no reconocido en '{msg.topic}': {data}")
        return

    # 3. Procesar tema de TELEMETRÍA
    if tipo_tema == "telemetria":
        es_valido, motivo = validar_telemetria(data)
        if not es_valido:
            print(f"[VALIDACIÓN RECHAZADA] Telemetría inválida de '{nodo_tema}': {motivo}")
            return

        # Solo tras validar la telemetría actualizamos el reloj de silencio y pasamos a ONLINE
        with lock_estado:
            ultima_telemetria_ts[nodo_tema] = time.time()

        registrar_cambio_estado(nodo_tema, "ONLINE", "Telemetría válida recibida")
        guardar_telemetria_csv(nodo_tema, data)
        print(f"[TELEMETRÍA OK] Nodo={nodo_tema} | Polvo={data['polvo']} µg/m³ | Gas={data['gas']} | Actuador={data.get('estado_actuador', data.get('actuador'))}")


def monitor_silencio():
    """Hilo que vigila si algún nodo supera UMBRAL_SILENCIO_SEG sin enviar telemetría válida."""
    while True:
        ahora = time.time()
        nodos_en_silencio = []

        with lock_estado:
            for nodo, estado_actual in list(estado_nodos.items()):
                ultimo_ts = ultima_telemetria_ts.get(nodo)
                if estado_actual == "ONLINE" and ultimo_ts is not None:
                    if (ahora - ultimo_ts) > UMBRAL_SILENCIO_SEG:
                        nodos_en_silencio.append((nodo, round(ahora - ultimo_ts, 1)))

        for nodo, segs in nodos_en_silencio:
            registrar_cambio_estado(
                nodo,
                "SIN_DATOS",
                f"Silencio de telemetría por {segs}s (> umbral de {UMBRAL_SILENCIO_SEG}s)"
            )

        time.sleep(0.5)


if __name__ == "__main__":
    inicializar_csvs()

    hilo_monitor = threading.Thread(target=monitor_silencio, daemon=True)
    hilo_monitor.start()

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="backend_ecoobra_receptor")
    cliente.on_connect = on_connect
    cliente.on_message = on_message

    print("[SISTEMA] Iniciando receptor Backend EcoObra...")
    try:
        cliente.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        cliente.loop_forever()
    except KeyboardInterrupt:
        print("\n[SISTEMA] Receptor detenido por el usuario.")
    except Exception as e:
        print(f"[ERROR CRÍTICO] Fallo en la conexión MQTT del backend: {e}")
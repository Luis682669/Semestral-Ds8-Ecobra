import machine
import time
import json
import ssd1306
import network
from umqtt.simple import MQTTClient

# ==========================================
# 1. Configuración de Red y MQTT
# ==========================================
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASS = ""
MQTT_BROKER = "broker.hivemq.com"
MQTT_CLIENT_ID = "ecoobra_esp32_luis"

TOPIC_BASE = "utp/ds8/equipo_luis/ecoobra/"
TOPIC_TELEMETRIA = b"utp/ds8/equipo_luis/ecoobra/telemetria"
TOPIC_ESTADO = b"utp/ds8/equipo_luis/ecoobra/estado"
TOPIC_ORDENES = b"utp/ds8/equipo_luis/ecoobra/ordenes"

# ==========================================
# 2. Hardware
# ==========================================
led_alerta = machine.Pin(2, machine.Pin.OUT)
boton_panico = machine.Pin(4, machine.Pin.IN, machine.Pin.PULL_UP)

# Sensores Analógicos
adc_polvo = machine.ADC(machine.Pin(34))
adc_polvo.atten(machine.ADC.ATTN_11DB)
adc_gas = machine.ADC(machine.Pin(35))
adc_gas.atten(machine.ADC.ATTN_11DB)

# Pantalla OLED
i2c = machine.I2C(0, scl=machine.Pin(22), sda=machine.Pin(21))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# Zumbador
buzzer = machine.PWM(machine.Pin(5))
buzzer.duty(0)

# ==========================================
# 3. Variables Globales
# ==========================================
panico_activado = False
umbral_polvo_critico = 100.0
cliente_mqtt = None
tiempo_ultimo_envio = 0
tiempo_ultimo_boton = 0  # Filtro anti-rebote

# ==========================================
# 4. Interrupciones y Funciones MQTT
# ==========================================
def manejar_interrupcion_panico(pin):
    global panico_activado, tiempo_ultimo_boton
    tiempo_actual = time.ticks_ms()
    
    # Filtro Anti-Rebote de 300 milisegundos
    if time.ticks_diff(tiempo_actual, tiempo_ultimo_boton) > 300:
        panico_activado = not panico_activado
        tiempo_ultimo_boton = tiempo_actual

boton_panico.irq(trigger=machine.Pin.IRQ_FALLING, handler=manejar_interrupcion_panico)

def callback_ordenes(topic, msg):
    global umbral_polvo_critico, panico_activado
    print("\n--- ORDEN RECIBIDA ---")
    try:
        payload = json.loads(msg.decode('utf-8'))
        
        if "nuevo_umbral_polvo" in payload:
            nuevo_valor = float(payload["nuevo_umbral_polvo"])
            if 50.0 <= nuevo_valor <= 300.0:
                umbral_polvo_critico = nuevo_valor
                print(f"Éxito: Umbral de polvo actualizado a {umbral_polvo_critico}")
            else:
                print("Error: Valor fuera de rango (50-300).")
                
        elif "desactivar_panico" in payload:
            if payload["desactivar_panico"] == True:
                panico_activado = False
                print("Éxito: Botón de pánico desactivado remotamente.")
        else:
            print("Error: Comando desconocido.")
    except Exception as e:
        print("Error: Formato JSON inválido.")

def conectar_mqtt():
    global cliente_mqtt
    try:
        cliente = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER)
        # Testamento MQTT (Last Will)
        cliente.set_last_will(TOPIC_ESTADO, b"OFFLINE", retain=True)
        cliente.set_callback(callback_ordenes)
        cliente.connect()
        cliente.publish(TOPIC_ESTADO, b"ONLINE", retain=True)
        cliente.subscribe(TOPIC_ORDENES)
        print("Conectado a MQTT.")
        return cliente
    except Exception as e:
        print("Fallo conectando a MQTT:", e)
        return None

# ==========================================
# 5. Bucle Principal
# ==========================================
wifi = network.WLAN(network.STA_IF)
wifi.active(True)
wifi.connect(WIFI_SSID, WIFI_PASS)

print("Iniciando EcoObra IoT...")

while True:
    tiempo_actual = time.ticks_ms()
    
    # Tolerancia a fallos: Si se cae el Wi-Fi
    if not wifi.isconnected():
        led_alerta.value(1)
        time.sleep(2)
        continue
        
    # Tolerancia a fallos: Si se cae el MQTT
    if cliente_mqtt is None:
        cliente_mqtt = conectar_mqtt()
        if cliente_mqtt is None:
            time.sleep(5)
            continue

    try:
        # Revisar si llegaron órdenes del supervisor
        cliente_mqtt.check_msg()
        
        # Enviar telemetría cada 2 segundos (2000 ms)
        if time.ticks_diff(tiempo_actual, tiempo_ultimo_envio) > 2000:
            polvo = round((adc_polvo.read() / 4095) * 150.0, 1)
            gas = round((adc_gas.read() / 4095) * 120.0, 1)
            
            estado_alarma = 1 if polvo > umbral_polvo_critico else 0
            nivel_texto = "SEGURO"
            
            # Lógica Inteligente de Alertas
            if panico_activado:
                buzzer.freq(1500)
                buzzer.duty(512)
                led_alerta.value(1)
                nivel_texto = "PANICO"
            elif estado_alarma:
                buzzer.freq(800)
                buzzer.duty(512)
                led_alerta.value(1)
                nivel_texto = "CRITICO"
            elif polvo > (umbral_polvo_critico * 0.75) or gas > 50:
                buzzer.duty(0)
                led_alerta.value(1)
                nivel_texto = "ALERTA"
            else:
                buzzer.duty(0)
                led_alerta.value(0)
                nivel_texto = "SEGURO"

            # Preparar y enviar JSON
            payload = {
                "polvo_pm10": polvo,
                "gas_voc": gas,
                "umbral_actual": umbral_polvo_critico,
                "boton_panico": panico_activado,
                "actuador_buzzer": estado_alarma,
                "nivel_riesgo": nivel_texto,
                "modo": "AUTO"
            }
            
            cliente_mqtt.publish(TOPIC_TELEMETRIA, json.dumps(payload))
            print("Datos enviados:", payload)
            tiempo_ultimo_envio = tiempo_actual
            
            # Actualizar Pantalla OLED Local
            oled.fill(0)
            oled.text("EcoObra IoT", 20, 0)
            oled.text(f"Polvo: {polvo}", 0, 20)
            oled.text(f"Gas: {gas}", 0, 30)
            oled.text(f"Est: {nivel_texto}", 0, 45)
            oled.text(f"Alarma: {estado_alarma}", 0, 55)
            oled.show()

    except OSError as e:
        print("Error de red, reconectando MQTT...")
        cliente_mqtt = None
        
    time.sleep(0.1)
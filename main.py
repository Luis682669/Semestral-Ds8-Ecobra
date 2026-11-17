import machine
import time
import network
import socket
import json
import struct
import _thread
from neopixel import NeoPixel

try:
    from umqtt.simple import MQTTClient
except ImportError:
    MQTTClient = None

try:
    from st7735 import ST7735
except ImportError:
    ST7735 = None

# --- CONFIGURACIÓN DE RED Y MQTT ---
WIFI_SSID = "TU_WIFI_SSID"          # Cambia por el nombre de tu red Wi-Fi
WIFI_PASS = "TU_WIFI_PASSWORD"      # Cambia por la contraseña de tu Wi-Fi

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
NODO_ID = "nodo1"

TOPIC_TELEMETRIA = f"ecoobra/{NODO_ID}/telemetria".encode('utf-8')
TOPIC_ESTADO     = f"ecoobra/{NODO_ID}/estado".encode('utf-8')
TOPIC_ORDENES    = f"ecoobra/{NODO_ID}/ordenes".encode('utf-8')

INTERVALO_TELEMETRIA = 2.0  # Publicación cada 2 segundos

# --- VARIABLES DE RECONEXIÓN CON ESPERA CRECIENTE (BACKOFF 5s -> 60s) ---
espera_reconex = 5
MAX_ESPERA_RECONEX = 60
ultimo_intento_red = 0
mqtt_conectado = False
cliente_mqtt = None

# --- UMBRALES Y VARIABLES GLOBALES DE TELEMETRÍA ---
umbral_polvo_critico = 250.0
polvo_actual = 18.5
pm25_actual = 8.2
gas_actual = 0.0
ruido_actual = 0.0
nivel_texto = "SEGURO"
estado_actuador = "OFF"
panico_activado = False

# --- CONFIGURACIÓN DE HARDWARE ---
# 1. Sensores Analógicos (MQ-135 en GPIO 4 y KY-038 en GPIO 5)
adc_gas = machine.ADC(machine.Pin(4))
adc_gas.atten(machine.ADC.ATTN_11DB)

adc_ruido = machine.ADC(machine.Pin(5))
adc_ruido.atten(machine.ADC.ATTN_11DB)

# 2. Botón Táctil de Pánico (TTP223 en GPIO 15)
boton_panico = machine.Pin(15, machine.Pin.IN, machine.Pin.PULL_DOWN)

# 3. Sensor de Polvo PMS5003 (UART1: TX=17, RX=18)
try:
    uart_pms = machine.UART(1, baudrate=9600, tx=machine.Pin(17), rx=machine.Pin(18))
except Exception:
    uart_pms = None

# 4. Pantalla TFT ST7735 (SoftSPI: SCK=12, MOSI=11, MISO=13, CS=10, DC=9, RST=8)
tft = None
if ST7735:
    try:
        spi = machine.SoftSPI(baudrate=20000000, polarity=0, phase=0,
                              sck=machine.Pin(12), mosi=machine.Pin(11), miso=machine.Pin(13))
        tft = ST7735(spi, cs=machine.Pin(10), dc=machine.Pin(9), rst=machine.Pin(8))
        tft.fill(0x0010)
    except Exception as e:
        print("[HARDWARE] Error iniciando TFT:", e)
        tft = None

# 5. LED RGB Integrado (Actuador visual en GPIO 48 / 38)
leds_internos = []
for pin_rgb in (48, 38):
    try:
        leds_internos.append(NeoPixel(machine.Pin(pin_rgb), 1))
    except Exception:
        pass

def color_led(r, g, b):
    for np in leds_internos:
        try:
            np[0] = (r, g, b)
            np.write()
        except Exception:
            pass

color_led(0, 40, 120)
time.sleep(0.3)
color_led(0, 60, 0)

# --- INTERRUPCIÓN BOTÓN DE PÁNICO ---
ultimo_toque = 0
def manejar_panico(pin):
    global panico_activado, ultimo_toque
    ahora = time.ticks_ms()
    if time.ticks_diff(ahora, ultimo_toque) > 400:
        panico_activado = not panico_activado
        ultimo_toque = ahora

boton_panico.irq(trigger=machine.Pin.IRQ_RISING, handler=manejar_panico)

# --- LECTURA UART PMS5003 SIN RESETEO DE DATOS ---
def leer_pms5003():
    if uart_pms and uart_pms.any() >= 32:
        datos = uart_pms.read()
        if datos:
            idx = datos.find(b'\x42\x4d')
            if idx != -1 and len(datos) >= idx + 32:
                trama = datos[idx:idx+32]
                if sum(trama[0:30]) == ((trama[30] << 8) | trama[31]):
                    pm1_0, pm2_5, pm10 = struct.unpack(">HHH", trama[10:16])
                    return float(pm2_5), float(pm10)
    return None

# --- RECEPCIÓN Y VALIDACIÓN ESTRICTA DE ÓRDENES MQTT ---
def callback_ordenes(topic, msg):
    """Separa errores de formato JSON de errores de validación de rangos y tipos."""
    global umbral_polvo_critico, panico_activado

    # 1. Comprobar exclusivamente si el mensaje es un JSON válido
    try:
        texto = msg.decode('utf-8')
        data = json.loads(texto)
    except Exception:
        print("[ERROR JSON] El mensaje recibido no tiene un formato JSON válido:", msg)
        return

    if not isinstance(data, dict):
        print("[ERROR VALIDACIÓN] El JSON de la orden debe ser un objeto clave-valor:", data)
        return

    orden_procesada = False

    # 2. Validar cambio de umbral de polvo (acepta 'umbral_polvo' o 'umbral')
    clave_umbral = "umbral_polvo" if "umbral_polvo" in data else ("umbral" if "umbral" in data else None)
    if clave_umbral:
        orden_procesada = True
        val = data[clave_umbral]
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            val_float = float(val)
            if 20.0 <= val_float <= 1000.0:
                umbral_polvo_critico = val_float
                print(f"[ORDEN APLICADA] Nuevo umbral de polvo crítico: {umbral_polvo_critico} µg/m³")
            else:
                print(f"[AVISO VALIDACIÓN] '{clave_umbral}' fuera de rango permitido (20.0 - 1000.0): {val}")
        else:
            print(f"[AVISO VALIDACIÓN] Tipo inválido para '{clave_umbral}' (se esperaba número): {val}")

    # 3. Validar explícitamente 'desactivar_panico' en todos sus casos
    if "desactivar_panico" in data:
        orden_procesada = True
        val_panico = data["desactivar_panico"]
        if isinstance(val_panico, bool):
            if val_panico is True:
                panico_activado = False
                print("[ORDEN APLICADA] Alerta de pánico desactivada remotamente.")
            else:
                print("[AVISO VALIDACIÓN] 'desactivar_panico' recibido en false: no produce acción (envíe true para desactivar).")
        else:
            print(f"[AVISO VALIDACIÓN] Valor inválido para 'desactivar_panico' (debe ser booleano true/false): {val_panico}")

    if not orden_procesada:
        print("[AVISO VALIDACIÓN] Orden JSON sin claves reconocidas ('umbral_polvo', 'desactivar_panico'):", data)

# --- GESTIÓN DE CONEXIÓN Y RECONEXIÓN EXPONENCIAL (HASTA 60S) ---
wlan_sta = network.WLAN(network.STA_IF)
wlan_sta.active(True)

def gestionar_reconexion():
    """Reintenta conectar Wi-Fi y MQTT con espera creciente (5s -> 10s -> 20s -> 40s -> 60s)."""
    global espera_reconex, ultimo_intento_red, mqtt_conectado, cliente_mqtt
    ahora = time.time()

    if ahora - ultimo_intento_red < espera_reconex:
        return False

    ultimo_intento_red = ahora

    # 1. Verificar y reconectar Wi-Fi explícitamente
    if not wlan_sta.isconnected():
        mqtt_conectado = False
        print(f"[RED] Wi-Fi desconectado. Intentando conectar a '{WIFI_SSID}' (próximo reintento en {espera_reconex}s)...")
        try:
            wlan_sta.disconnect()
        except Exception:
            pass
        try:
            wlan_sta.connect(WIFI_SSID, WIFI_PASS)
        except Exception as e:
            print("[RED] Aviso al iniciar conexión Wi-Fi:", e)
        espera_reconex = min(MAX_ESPERA_RECONEX, espera_reconex * 2)
        return False

    # 2. Conectar o reconectar al broker MQTT con Last Will y mensaje ONLINE retenido
    if MQTTClient is None:
        return False

    try:
        print(f"[MQTT] Intentando conexión al broker {MQTT_BROKER}...")
        client_id = f"ecoobra_{NODO_ID}_{machine.unique_id().hex()}"
        cliente_mqtt = MQTTClient(client_id, MQTT_BROKER, port=MQTT_PORT, keepalive=6)
        cliente_mqtt.set_callback(callback_ordenes)
        # Configurar Last Will (OFFLINE retenido)
        testamento = json.dumps({"nodo": NODO_ID, "estado": "OFFLINE"}).encode('utf-8')
        cliente_mqtt.set_last_will(TOPIC_ESTADO, testamento, retain=True, qos=1)
        cliente_mqtt.connect()
        # Publicar estado ONLINE retenido al conectar
        msg_online = json.dumps({"nodo": NODO_ID, "estado": "ONLINE"}).encode('utf-8')
        cliente_mqtt.publish(TOPIC_ESTADO, msg_online, retain=True, qos=1)
        cliente_mqtt.subscribe(TOPIC_ORDENES)
        mqtt_conectado = True
        espera_reconex = 5  # Reiniciar espera creciente tras éxito
        print("[MQTT] Conectado exitosamente. Estado ONLINE publicado y espera reiniciada a 5s.")
        return True
    except Exception as e:
        mqtt_conectado = False
        print(f"[MQTT] Fallo al conectar ({e}). Próximo reintento en {espera_reconex}s.")
        espera_reconex = min(MAX_ESPERA_RECONEX, espera_reconex * 2)
        return False

# --- HILO PRINCIPAL DE SENSORES, CONTROL LOCAL Y TELEMETRÍA MQTT ---
def tarea_sensores_y_mqtt():
    global polvo_actual, pm25_actual, gas_actual, ruido_actual
    global nivel_texto, estado_actuador, panico_activado, mqtt_conectado

    paso = False
    ultima_publicacion = 0

    while True:
        try:
            # A. Lectura local ininterrumpida (opera aún sin Wi-Fi ni MQTT)
            gas_actual = round((adc_gas.read() / 4095.0) * 120.0, 1)
            lectura_ruido = round((adc_ruido.read() / 4095.0) * 500.0, 1)
            ruido_actual = round(35.0 + (lectura_ruido / 500.0) * 65.0, 1)

            datos_pms = leer_pms5003()
            if datos_pms:
                pm25_actual, polvo_actual = datos_pms

            # B. Evaluación local de estados y accionamiento del actuador/pantalla
            paso = not paso
            if panico_activado:
                nivel_texto = "PANICO"
                estado_actuador = "ON"
                color_led(255, 0, 0) if paso else color_led(30, 0, 0)
                if tft: tft.fill(0xF800)
            elif polvo_actual >= umbral_polvo_critico or gas_actual >= 90.0:
                nivel_texto = "CRITICO"
                estado_actuador = "ON"
                color_led(200, 0, 0)
                if tft: tft.fill(0xF800)
            elif polvo_actual >= (umbral_polvo_critico * 0.75) or gas_actual >= 60.0:
                nivel_texto = "ALERTA"
                estado_actuador = "ON"
                color_led(180, 100, 0)
                if tft: tft.fill(0xFD20)
            else:
                nivel_texto = "SEGURO"
                estado_actuador = "OFF"
                color_led(0, 80, 0)
                if tft: tft.fill(0x03E0)

            # C. Gestión de red y envío de telemetría cada 2 segundos
            ahora_ms = time.ticks_ms()
            if not wlan_sta.isconnected() or not mqtt_conectado:
                mqtt_conectado = False
                gestionar_reconexion()
            else:
                try:
                    cliente_mqtt.check_msg()
                    if time.ticks_diff(ahora_ms, ultima_publicacion) >= int(INTERVALO_TELEMETRIA * 1000):
                        payload = json.dumps({
                            "nodo": NODO_ID,
                            "polvo": polvo_actual,
                            "pm25": pm25_actual,
                            "gas": gas_actual,
                            "ruido": ruido_actual,
                            "estado_actuador": estado_actuador,
                            "actuador": estado_actuador,
                            "nivel": nivel_texto,
                            "panico": panico_activado,
                            "umbral_polvo": umbral_polvo_critico
                        })
                        cliente_mqtt.publish(TOPIC_TELEMETRIA, payload.encode('utf-8'))
                        ultima_publicacion = ahora_ms
                except Exception as e:
                    print("[MQTT] Conexión perdida durante operación:", e)
                    mqtt_conectado = False

        except Exception as e:
            print("[LOCAL] Error en ciclo de sensores:", e)

        time.sleep(0.5)

# --- PANEL WEB LOCAL (ACCESS POINT EN 192.168.4.1) ---
HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EcoObra OS | Telemetría IoT</title>
<style>
:root{--neon:#38bdf8;--glow:rgba(56,189,248,0.18);}
*{box-sizing:border-box;margin:0;padding:0;}
@keyframes gridMove{0%{background-position:0 0,0 0;}100%{background-position:0 40px,40px 0;}}
@keyframes spin{100%{transform:rotate(360deg);}}
@keyframes pulse{0%,100%{box-shadow:0 0 12px var(--glow);}50%{box-shadow:0 0 25px var(--glow);}}
@keyframes strobe{0%,100%{box-shadow:inset 0 0 0 transparent;}50%{box-shadow:inset 0 0 55px rgba(239,68,68,0.45);}}
@keyframes shake{0%,100%{transform:translateX(0);}25%{transform:translateX(-4px);}75%{transform:translateX(4px);}}
@keyframes eq{0%,100%{height:6px;}50%{height:22px;}}
body{font-family:system-ui,sans-serif;background:#03090e;background-image:linear-gradient(rgba(56,189,248,0.06) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,0.06) 1px,transparent 1px);background-size:40px 40px;animation:gridMove 3s linear infinite;color:#f1f5f9;min-height:100vh;padding:14px;}
body.critico{--neon:#ef4444;--glow:rgba(239,68,68,0.3);animation:gridMove 1.5s linear infinite,strobe 1s infinite;}
body.alerta{--neon:#f59e0b;--glow:rgba(245,158,11,0.25);}
.c{max-width:460px;margin:0 auto;}
.hdr{display:flex;align-items:center;justify-content:space-between;background:rgba(10,25,36,0.88);border:1px solid var(--neon);border-radius:16px;padding:12px 16px;margin-bottom:14px;animation:pulse 3s infinite;}
.radar{width:38px;height:38px;border-radius:50%;border:2px solid var(--neon);position:relative;overflow:hidden;}
.radar::after{content:'';position:absolute;inset:0;background:conic-gradient(from 0deg,transparent 65%,var(--neon) 100%);animation:spin 2s linear infinite;border-radius:50%;}
.card{background:rgba(11,24,35,0.85);border:1px solid rgba(56,189,248,0.25);border-radius:16px;padding:15px;margin-bottom:12px;backdrop-filter:blur(8px);transition:0.3s;}
.lbl{font-size:0.78rem;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;font-weight:600;}
.vrow{display:flex;align-items:baseline;justify-content:space-between;margin:6px 0 10px;}
.val{font-size:2.1rem;font-weight:800;color:#fff;text-shadow:0 0 10px var(--glow);}
.unit{font-size:0.9rem;color:var(--neon);margin-left:4px;}
.pill{background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.3);padding:3px 9px;border-radius:8px;font-size:0.78rem;color:#bae6fd;}
.bar{width:100%;height:8px;background:#0f172a;border-radius:8px;overflow:hidden;}
.fill{height:100%;width:15%;background:linear-gradient(90deg,#0ea5e9,#10b981,#f59e0b,#ef4444);background-size:200% 100%;transition:width 0.4s ease;}
.badge{display:inline-block;padding:6px 24px;border-radius:50px;font-weight:800;font-size:1.1rem;letter-spacing:2px;margin-top:6px;}
.b-seg{background:rgba(16,185,129,0.2);color:#34d399;border:1px solid #10b981;}
.b-ale{background:rgba(245,158,11,0.2);color:#fbbf24;border:1px solid #f59e0b;}
.b-cri{background:rgba(239,68,68,0.25);color:#f87171;border:1px solid #ef4444;animation:shake 0.4s infinite;}
.eq{display:flex;align-items:flex-end;gap:3px;height:20px;}
.eq i{width:4px;background:#38bdf8;border-radius:2px;animation:eq 0.7s infinite;}
.eq i:nth-child(2){animation-delay:0.2s;}.eq i:nth-child(3){animation-delay:0.4s;}
.btn{width:100%;padding:10px;border-radius:10px;border:1px solid #ef4444;background:rgba(239,68,68,0.2);color:#fff;font-weight:700;margin-top:8px;display:none;}
canvas{width:100%;height:95px;display:block;margin-top:8px;}
</style></head>
<body><div class="c">
  <div class="hdr">
    <div style="display:flex;align-items:center;gap:12px;">
      <div class="radar"></div>
      <div><div style="font-weight:800;font-size:1.15rem;text-shadow:0 0 8px var(--neon);">ECOOBRA OS</div>
      <div style="font-size:0.72rem;color:#34d399;">● TELEMETRÍA EN VIVO</div></div>
    </div>
    <div id="clk" style="font-family:monospace;color:#38bdf8;">--:--:--</div>
  </div>
  <div class="card" style="text-align:center;">
    <div class="lbl">Estado de Seguridad</div>
    <div id="bdg" class="badge b-seg">SEGURO</div>
    <button id="btnR" class="btn" onclick="fetch('/reset_panico')">RESETEAR ALERTA PÁNICO</button>
  </div>
  <div class="card">
    <div class="lbl">Polvo PM10 (PMS5003)</div>
    <div class="vrow"><div><span class="val" id="p">0</span><span class="unit">µg/m³</span></div><div class="pill" id="pm25">PM2.5: 0</div></div>
    <div class="bar"><div class="fill" id="bp"></div></div>
  </div>
  <div class="card">
    <div class="lbl">Gas VOC (MQ-135)</div>
    <div class="vrow"><div><span class="val" id="g">0</span><span class="unit">/ 120</span></div><div class="pill" id="gs">Normal</div></div>
    <div class="bar"><div class="fill" id="bg"></div></div>
  </div>
  <div class="card">
    <div style="display:flex;justify-content:space-between;"><span class="lbl">Ruido Ambiental (KY-038)</span><div class="eq"><i></i><i></i><i></i></div></div>
    <div class="vrow"><div><span class="val" id="r">0</span><span class="unit">dB</span></div><div class="pill">Acústica</div></div>
    <div class="bar"><div class="fill" id="br"></div></div>
  </div>
  <div class="card">
    <div class="lbl">Osciloscopio Multiparámetro (30s)</div>
    <canvas id="cv" width="400" height="95"></canvas>
  </div>
</div>
<script>
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
let hp=Array(25).fill(18),hg=Array(25).fill(20),hr=Array(25).fill(40);
setInterval(async()=>{
  document.getElementById('clk').innerText=new Date().toLocaleTimeString();
  try{
    let res=await fetch('/datos'),d=await res.json();
    document.getElementById('p').innerText=d.polvo;
    document.getElementById('pm25').innerText='PM2.5: '+d.pm25+' µg/m³';
    document.getElementById('g').innerText=d.gas;
    document.getElementById('r').innerText=d.ruido;
    document.getElementById('bp').style.width=Math.min(100,(d.polvo/250)*100)+'%';
    document.getElementById('bg').style.width=Math.min(100,(d.gas/120)*100)+'%';
    document.getElementById('br').style.width=Math.min(100,d.ruido)+'%';
    let b=document.getElementById('bdg'),br=document.getElementById('btnR');
    b.innerText=d.nivel; b.className='badge'; document.body.className='';
    if(d.nivel==='SEGURO'){b.classList.add('b-seg');br.style.display='none';}
    else if(d.nivel==='ALERTA'){b.classList.add('b-ale');document.body.classList.add('alerta');br.style.display='none';}
    else{b.classList.add('b-cri');document.body.classList.add('critico');br.style.display=d.nivel==='PANICO'?'block':'none';}
    hp.push(d.polvo);hp.shift();hg.push(d.gas);hg.shift();hr.push(d.ruido);hr.shift();
    ctx.clearRect(0,0,cv.width,cv.height);
    [[hp,260,'#38bdf8'],[hg,120,'#a855f7'],[hr,100,'#10b981']].forEach(([a,m,c])=>{
      ctx.beginPath();ctx.strokeStyle=c;ctx.lineWidth=2;
      a.forEach((v,i)=>{let x=(i/24)*cv.width,y=cv.height-Math.min(1,v/m)*(cv.height-12)-4;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
      ctx.stroke();
    });
  }catch(e){}
},1000);
</script></body></html>"""

ap = network.WLAN(network.AP_IF)
ap.active(True)
ap.config(essid="EcoObra-Local", authmode=network.AUTH_OPEN)
while not ap.active():
    time.sleep(0.1)

print("Red AP Creada: EcoObra-Local | Panel Web: http://192.168.4.1")
_thread.start_new_thread(tarea_sensores_y_mqtt, ())

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('', 80))
s.listen(5)

while True:
    conn = None
    try:
        conn, addr = s.accept()
        conn.settimeout(2.0)
        req = conn.recv(512).decode('utf-8')

        if "GET /datos" in req:
            res = json.dumps({
                "polvo": polvo_actual,
                "pm25": pm25_actual,
                "gas": gas_actual,
                "ruido": ruido_actual,
                "nivel": nivel_texto,
                "estado_actuador": estado_actuador
            })
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n" + res.encode('utf-8'))
        elif "GET /reset_panico" in req:
            panico_activado = False
            conn.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nOK")
        else:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n" + HTML_PAGE.encode('utf-8'))
    except Exception:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
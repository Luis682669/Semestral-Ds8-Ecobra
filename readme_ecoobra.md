# 🏗️ EcoObra IoT - Sistema de Monitoreo para Construcciones

**Universidad Tecnológica de Panamá (UTP)**
**Facultad de Ingeniería de Sistemas Computacionales**
**Asignatura:** Herramientas de Programación Aplicada IV
**Facilitador:** Prof. José Collado
**Equipo:** Luis, Juan, Jean

---

## 📖 Descripción del Proyecto
**EcoObra IoT** es un nodo de monitoreo ambiental diseñado para obras de construcción. Su objetivo es medir en tiempo real los niveles de polvo (PM10) y gases (VOC) para proteger la salud de los trabajadores y comunidades aledañas. El sistema cuenta con alarmas locales (visuales y sonoras), un botón de pánico físico y comunicación en tiempo real hacia la nube mediante MQTT.

Este repositorio contiene el **Avance 3**, enfocado en la resiliencia de comunicación, telemetría bidireccional y monitoreo de estado.

## ✨ Características de este Avance (Módulo de Comunicación)
- **Telemetría JSON:** Transmisión de lecturas de sensores y estado de actuadores cada 2 segundos.
- **Tolerancia a fallos:** El ESP32 es capaz de detectar caídas de Wi-Fi o del Broker MQTT y reconectarse automáticamente sin detener su funcionamiento local.
- **Last Will (Testamento):** Si el nodo pierde energía repentinamente, el broker notifica automáticamente su estado `OFFLINE`.
- **Recepción de Órdenes:** El dispositivo acepta comandos remotos para cambiar el umbral de las alarmas o desactivar el estado de pánico.
- **Backend Monitor:** Un script en Python que recibe, valida y guarda los datos en archivos CSV, además de registrar eventos de conexión (`ONLINE`, `SIN_DATOS`, `OFFLINE`).

---

## 🗂️ Esquema de Temas MQTT

| Tema | Dirección | Contenido (Payload) | Retenido |
| :--- | :--- | :--- | :---: |
| `utp/ds8/equipo_luis/ecoobra/telemetria` | Dispositivo -> Broker | JSON con polvo, gas, umbral y estado de alarmas | No |
| `utp/ds8/equipo_luis/ecoobra/estado` | Dispositivo <-> Broker | Texto: `ONLINE` o `OFFLINE` (Last Will) | Sí |
| `utp/ds8/equipo_luis/ecoobra/ordenes` | Broker -> Dispositivo | JSON con `nuevo_umbral_polvo` o `desactivar_panico` | No |

---

## 🚀 Instrucciones de Ejecución

### 1. Requisitos Previos (Backend)
Para ejecutar la estación receptora en su computadora, necesita tener Python instalado y la librería `paho-mqtt`.
Ejecute el siguiente comando en su terminal para instalar las dependencias:
```bash
pip install -r requirements.txt
```

### 2. Ejecutar el Nodo (Simulador Wokwi)
1. Abra el proyecto en Wokwi (Enlace proporcionado en la entrega).
2. Asegúrese de que los archivos `main.py`, `diagram.json` y `ssd1306.py` estén correctamente cargados.
3. Presione el botón **Play**. El ESP32 se conectará al Wi-Fi simulado y luego al broker público de HiveMQ.

### 3. Ejecutar el Backend Receptor (VS Code)
1. Abra la terminal en el directorio de este repositorio.
2. Ejecute el script del monitor:
```bash
python backend_ecoobra.py
```
3. El programa comenzará a escuchar los datos en tiempo real, mostrará las alertas en consola y generará automáticamente los archivos `telemetria.csv` y `eventos.csv`.

---

## 🛠️ Ejemplos de Órdenes Remotas (JSON)

Para probar la recepción de órdenes, publique los siguientes mensajes en el tema `utp/ds8/equipo_luis/ecoobra/ordenes`:

**Cambiar umbral de polvo (Rango permitido 50-300):**
```json
{"nuevo_umbral_polvo": 120}
```

**Desactivar botón de pánico remotamente:**
```json
{"desactivar_panico": true}
```
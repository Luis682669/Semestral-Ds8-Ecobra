EcoObra IoT - Sistema de Monitoreo para Construcciones

**Universidad Tecnológica de Panamá (UTP)**
**Facultad de Ingeniería de Sistemas Computacionales**
**Asignatura:** Herramientas de Programación Aplicada IV
**Facilitador:** Prof. José Collado
**Equipo:** Luis De Los Rios, Juan Segundo, Jean Batista

---

## Descripción del Proyecto
**EcoObra IoT** es un nodo de monitoreo ambiental diseñado para obras de construcción. Su objetivo es medir en tiempo real los niveles de polvo (PM10) y gases (VOC) para proteger la salud de los trabajadores y comunidades aledañas. El sistema cuenta con alarmas locales (visuales y sonoras), un botón de pánico físico y comunicación en tiempo real hacia la nube mediante MQTT.

Este repositorio contiene el **Avance 3**, enfocado en la resiliencia de comunicación, telemetría bidireccional y monitoreo de estado.

## Características de este Avance (Módulo de Comunicación)
- **Telemetría JSON:** Transmisión de lecturas de sensores y estado de actuadores cada 2 segundos.
- **Tolerancia a fallos:** El ESP32 es capaz de detectar caídas de Wi-Fi o del Broker MQTT y reconectarse automáticamente sin detener su funcionamiento local.
- **Last Will (Testamento):** Si el nodo pierde energía repentinamente, el broker notifica automáticamente su estado `OFFLINE`.
- **Recepción de Órdenes:** El dispositivo acepta comandos remotos para cambiar el umbral de las alarmas o desactivar el estado de pánico.
- **Backend Monitor:** Un script en Python que recibe, valida y guarda los datos en archivos CSV, además de registrar eventos de conexión (`ONLINE`, `SIN_DATOS`, `OFFLINE`).

---

## Esquema de Temas MQTT

| Tema | Dirección | Contenido (Payload) | Retenido |
| :--- | :--- | :--- | :---: |
| `utp/ds8/equipo_luis/ecoobra/telemetria` | Dispositivo -> Broker | JSON con polvo, gas, umbral y estado de alarmas | No |
| `utp/ds8/equipo_luis/ecoobra/estado` | Dispositivo <-> Broker | Texto: `ONLINE` o `OFFLINE` (Last Will) | Sí |
| `utp/ds8/equipo_luis/ecoobra/ordenes` | Broker -> Dispositivo | JSON con `nuevo_umbral_polvo` o `desactivar_panico` | No |

---

## Instrucciones de Ejecución

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



## Ejemplos de Órdenes Remotas (JSON)

Para probar la recepción de órdenes, publique los siguientes mensajes en el tema `utp/ds8/equipo_luis/ecoobra/ordenes`:

**Cambiar umbral de polvo (Rango permitido 50-300):**
```json
{"nuevo_umbral_polvo": 120}
```

**Desactivar botón de pánico remotamente:**
```json
{"desactivar_panico": true}
```
## Comportamiento Local ante Fallos de Conexión
Mientras el dispositivo no tiene conexión Wi-Fi o MQTT:
1. **Operación autónoma ininterrumpida:** El bucle de sensores sigue leyendo polvo, gas y botón de pánico cada 2 segundos y accionando los indicadores locales (LED RGB / pantalla TFT y actuador) sin bloquearse.
2. **Reconexión con espera creciente (Exponential Backoff):** El nodo intenta reconectar el Wi-Fi y el cliente MQTT iniciando en 5 segundos y duplicando el tiempo en cada fallo (5 s, 10 s, 20 s, 40 s) hasta un tope máximo de 60 segundos, restableciéndose a 5 segundos en cuanto vuelve la conexión.

## Registro de Pruebas Documentadas

| Escenario | Cómo se provocó | Resultado esperado | Resultado observado |
| :--- | :--- | :--- | :--- |
| 1. Telemetría nominal | Encendido del nodo con aire limpio conectado al broker | Publicación JSON cada 2 s, validación exitosa en receptor, estado `ONLINE` y guardado en CSV | Se reciben tramas cada 2 s, el backend valida rangos de polvo/gas y las registra en el CSV |
| 2. Cambio de umbral válido | Publicación MQTT de `{"umbral_polvo": 180}` en el tema de órdenes | El nodo actualiza el umbral y muestra confirmación en consola | Umbral actualizado a 180.0 µg/m³ inmediatamente sin errores |
| 3. Orden con valor o JSON inválido | Envío de `{"desactivar_panico": "texto"}` y luego `{json_roto` | Avisos diferenciados: error de validación de tipo booleano en el primero y error de formato JSON en el segundo | Consola distingue explícitamente el aviso de validación de `desactivar_panico` frente al error de sintaxis JSON |
| 4. Telemetría fuera de rango | Envío manual al tema de telemetría de `{"polvo": 50, "gas": -25, "estado_actuador": "SEGURO"}` | El receptor descarta el mensaje por gas negativo, no guarda en CSV y no actualiza el estado del monitor | Mensaje rechazado por el validador de rangos sin detener la ejecución de `backend_ecoobra.py` |
| 5. Silencio de datos (`SIN_DATOS`) | Pausa del envío de telemetría manteniendo la conexión TCP activa por más de 7 segundos | Al superar el umbral configurable de 7 s (> 3 intervalos), el monitor registra el cambio a `SIN_DATOS` | El monitor detectó los 7 s de silencio y registró la transición `ONLINE` -> `SIN_DATOS` en el CSV de eventos |
| 6. Caída abrupta y Last Will (`OFFLINE`) | Desconexión física de la energía del ESP32 | El broker publica el testamento `OFFLINE` retenido y el receptor registra el cambio a `OFFLINE` | El receptor capturó el Last Will y registró el evento `OFFLINE` de inmediato |
| 7. Reconexión con Backoff | Apagado temporal del broker MQTT durante 40 segundos | El nodo sigue evaluando alertas localmente y espacia los reintentos (5 s, 10 s, 20 s...) hasta reconectar y publicar `ONLINE` | Los sensores locales siguieron activos y la reconexión se efectuó con espera creciente hasta restaurar `ONLINE` |
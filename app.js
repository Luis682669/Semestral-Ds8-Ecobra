const BROKER_URL = 'wss://broker.hivemq.com:8884/mqtt';
const TOPIC_ROOT = 'utp/ds8/equipo_luis/ecoobra';
const TOPIC_TELEMETRIA = `${TOPIC_ROOT}/telemetria`;
const TOPIC_ESTADO = `${TOPIC_ROOT}/estado`;
const MAX_POINTS = 30;
const STALE_AFTER_MS = 7000;

const elements = {
    connectionBadge: document.getElementById('connectionBadge'),
    connectionDot: document.getElementById('connectionDot'),
    connectionText: document.getElementById('connectionText'),
    connectionButton: document.getElementById('connectionButton'),
    connectionButtonText: document.getElementById('connectionButtonText'),
    pm10Value: document.getElementById('pm10Value'),
    pm10Caption: document.getElementById('pm10Caption'),
    vocValue: document.getElementById('vocValue'),
    vocCaption: document.getElementById('vocCaption'),
    alarmCard: document.getElementById('alarmCard'),
    alarmValue: document.getElementById('alarmValue'),
    alarmIcon: document.getElementById('alarmIcon'),
    alarmCaption: document.getElementById('alarmCaption'),
    lastReading: document.getElementById('lastReading'),
    deviceState: document.getElementById('deviceState'),
    eventLog: document.getElementById('eventLog'),
    emptyLog: document.getElementById('emptyLog')
};

let client = null;
let chart = null;
let lastTelemetryAt = null;
let intentionalDisconnect = false;

function setConnectionStatus(status, label) {
    const styles = {
        connected: {
            badge: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
            dot: 'bg-emerald-400',
            button: 'bg-rose-500 hover:bg-rose-400'
        },
        connecting: {
            badge: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
            dot: 'bg-amber-400 animate-pulse',
            button: 'bg-slate-700 hover:bg-slate-600'
        },
        disconnected: {
            badge: 'border-slate-700 bg-slate-900 text-slate-400',
            dot: 'bg-slate-500',
            button: 'bg-emerald-500 hover:bg-emerald-400'
        }
    };
    const style = styles[status];
    elements.connectionBadge.className = `inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs font-semibold ${style.badge}`;
    elements.connectionDot.className = `status-dot h-2 w-2 rounded-full ${style.dot}`;
    elements.connectionText.textContent = label;
    elements.connectionButton.className = `rounded-xl px-4 py-2.5 text-xs font-bold text-slate-950 transition focus:outline-none focus:ring-2 focus:ring-emerald-300 ${style.button}`;
    elements.connectionButtonText.textContent = status === 'connected' ? 'Desconectar' : status === 'connecting' ? 'Conectando...' : 'Conectar';
    elements.connectionButton.disabled = status === 'connecting';
    elements.connectionButton.classList.toggle('cursor-not-allowed', status === 'connecting');
}

function addLogEntry(message, kind = 'info') {
    if (elements.emptyLog) {
        elements.emptyLog.remove();
        elements.emptyLog = null;
    }

    const colors = {
        info: 'text-cyan-300',
        online: 'text-emerald-300',
        alert: 'text-rose-300',
        warning: 'text-amber-300'
    };
    const entry = document.createElement('div');
    entry.className = 'flex gap-3 rounded-xl border border-slate-800/80 bg-slate-950/40 px-3 py-2.5 text-xs';

    const time = document.createElement('time');
    time.className = 'shrink-0 font-mono text-slate-500';
    time.textContent = new Date().toLocaleTimeString('es-PA', { hour12: false });

    const text = document.createElement('span');
    text.className = `min-w-0 break-words ${colors[kind] || colors.info}`;
    text.textContent = message;

    entry.append(time, text);
    elements.eventLog.prepend(entry);
    while (elements.eventLog.children.length > 40) {
        elements.eventLog.lastElementChild.remove();
    }
}

function setDeviceState(value) {
    const online = value === 'ONLINE';
    elements.deviceState.textContent = `Estado del dispositivo: ${online ? 'En lÃ­nea' : 'Fuera de lÃ­nea'}`;
    elements.deviceState.className = `mt-4 text-xs ${online ? 'text-emerald-300' : 'text-rose-300'}`;
    addLogEntry(`Dispositivo ${online ? 'ONLINE' : 'OFFLINE'}`, online ? 'online' : 'alert');
}

function normalizeAlarm(value) {
    if (value === true || value === 1 || value === '1' || value === 'true') return true;
    if (value === false || value === 0 || value === '0' || value === 'false') return false;
    return null;
}

function handleTelemetry(payload) {
    let data;
    try {
        data = JSON.parse(payload);
    } catch (error) {
        addLogEntry('TelemetrÃ­a descartada: el mensaje no contiene JSON vÃ¡lido.', 'warning');
        return;
    }

    const pm10 = Number(data.polvo_pm10);
    const voc = Number(data.gas_voc);
    if (!Number.isFinite(pm10) || !Number.isFinite(voc) || pm10 < 0 || pm10 > 1000 || voc < 0) {
        addLogEntry('TelemetrÃ­a descartada: valores PM10 o VOC invÃ¡lidos.', 'warning');
        return;
    }

    lastTelemetryAt = Date.now();
    elements.pm10Value.textContent = pm10.toLocaleString('es-PA', { maximumFractionDigits: 1 });
    elements.vocValue.textContent = voc.toLocaleString('es-PA', { maximumFractionDigits: 1 });
    elements.pm10Caption.textContent = 'Lectura recibida del sensor de polvo';
    elements.vocCaption.textContent = 'Lectura recibida del sensor de gas';
    elements.lastReading.textContent = new Date(lastTelemetryAt).toLocaleTimeString('es-PA', { hour12: false });
    elements.deviceState.textContent = 'Estado del dispositivo: En lÃ­nea';
    elements.deviceState.className = 'mt-4 text-xs text-emerald-300';

    const alarm = normalizeAlarm(data.actuador_buzzer);
    if (alarm === null) {
        elements.alarmValue.textContent = 'Sin dato';
        elements.alarmValue.className = 'metric-value mt-5 text-2xl font-bold text-slate-300';
        elements.alarmCaption.textContent = 'El mensaje no incluyÃ³ actuador_buzzer';
        elements.alarmCard.className = 'panel rounded-2xl p-5';
        elements.alarmIcon.className = 'flex h-10 w-10 items-center justify-center rounded-xl bg-slate-800 text-slate-400';
    } else {
        elements.alarmValue.textContent = alarm ? 'ACTIVADA' : 'Inactiva';
        elements.alarmValue.className = `metric-value mt-5 text-2xl font-bold ${alarm ? 'text-rose-300' : 'text-emerald-300'}`;
        elements.alarmCaption.textContent = alarm ? 'El buzzer estÃ¡ indicando una alerta' : 'El buzzer no estÃ¡ activo';
        elements.alarmCard.className = `panel rounded-2xl p-5 ${alarm ? 'border-rose-500/40' : ''}`;
        elements.alarmIcon.className = `flex h-10 w-10 items-center justify-center rounded-xl ${alarm ? 'bg-rose-500/10 text-rose-300' : 'bg-emerald-500/10 text-emerald-300'}`;
    }

    if (chart) {
        const timestamp = new Date(lastTelemetryAt).toLocaleTimeString('es-PA', { hour12: false });
        chart.data.labels.push(timestamp);
        chart.data.datasets[0].data.push(pm10);
        chart.data.datasets[1].data.push(voc);
        while (chart.data.labels.length > MAX_POINTS) {
            chart.data.labels.shift();
            chart.data.datasets.forEach(dataset => dataset.data.shift());
        }
        chart.update();
    }

    addLogEntry(`TelemetrÃ­a Â· PM10 ${pm10} Âµg/mÂ³ Â· VOC ${voc} ppm Â· alarma ${alarm === null ? 'sin dato' : alarm ? 'activa' : 'inactiva'}`, alarm ? 'alert' : 'info');
}

function handleMessage(topic, payload) {
    if (topic === TOPIC_ESTADO) {
        const deviceStatus = payload.trim().toUpperCase();
        if (deviceStatus === 'ONLINE' || deviceStatus === 'OFFLINE') {
            setDeviceState(deviceStatus);
        } else {
            addLogEntry(`Estado descartado: valor no reconocido (${deviceStatus}).`, 'warning');
        }
        return;
    }

    if (topic === TOPIC_TELEMETRIA) {
        handleTelemetry(payload);
    }
}

function connectMqtt() {
    if (typeof mqtt === 'undefined') {
        setConnectionStatus('disconnected', 'Error: MQTT.js no cargÃ³');
        addLogEntry('No se pudo cargar MQTT.js. Revisa la conexiÃ³n a Internet y recarga la pÃ¡gina.', 'warning');
        return;
    }

    intentionalDisconnect = false;
    setConnectionStatus('connecting', 'Conectando...');
    const clientId = `ecoobra-dashboard-${Math.random().toString(16).slice(2, 10)}`;
    client = mqtt.connect(BROKER_URL, {
        clientId,
        clean: true,
        connectTimeout: 10000,
        reconnectPeriod: 3000
    });

    client.on('connect', () => {
        setConnectionStatus('connected', 'Conectado a HiveMQ');
        addLogEntry('Conectado a HiveMQ; suscribiendo a los temas de EcoObra.', 'online');
        client.subscribe(`${TOPIC_ROOT}/#`, { qos: 0 }, error => {
            if (error) {
                addLogEntry(`Error al suscribirse a MQTT: ${error.message}`, 'warning');
            } else {
                addLogEntry(`SuscripciÃ³n activa: ${TOPIC_ROOT}/#`, 'info');
            }
        });
    });

    client.on('message', (topic, message) => {
        handleMessage(topic, message.toString());
    });

    client.on('reconnect', () => {
        setConnectionStatus('connecting', 'Reconectando...');
    });

    client.on('offline', () => {
        if (!intentionalDisconnect) {
            setConnectionStatus('disconnected', 'Sin conexiÃ³n con HiveMQ');
        }
    });

    client.on('error', error => {
        console.error('Error MQTT:', error);
        addLogEntry(`Error MQTT: ${error.message}`, 'warning');
    });

    client.on('close', () => {
        if (!intentionalDisconnect) {
            setConnectionStatus('disconnected', 'Desconectado');
        }
    });
}

function disconnectMqtt() {
    intentionalDisconnect = true;
    if (client) {
        client.end(true);
        client = null;
    }
    setConnectionStatus('disconnected', 'Desconectado');
    addLogEntry('ConexiÃ³n MQTT cerrada.', 'info');
}

function initializeChart() {
    const context = document.getElementById('readingsChart').getContext('2d');
    chart = new Chart(context, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Polvo PM10 (Âµg/mÂ³)',
                    data: [],
                    yAxisID: 'pm10',
                    borderColor: '#fbbf24',
                    backgroundColor: 'rgba(251, 191, 36, .12)',
                    borderWidth: 2,
                    pointRadius: 2,
                    tension: .3
                },
                {
                    label: 'Gas VOC (ppm)',
                    data: [],
                    yAxisID: 'voc',
                    borderColor: '#22d3ee',
                    backgroundColor: 'rgba(34, 211, 238, .1)',
                    borderWidth: 2,
                    pointRadius: 2,
                    tension: .3
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 180 },
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    labels: {
                        color: '#cbd5e1',
                        usePointStyle: true,
                        boxWidth: 8,
                        padding: 18
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(148, 163, 184, .08)' },
                    ticks: { color: '#64748b', maxRotation: 0, autoSkip: true }
                },
                pm10: {
                    type: 'linear',
                    position: 'left',
                    beginAtZero: true,
                    title: { display: true, text: 'PM10 (Âµg/mÂ³)', color: '#fbbf24' },
                    grid: { color: 'rgba(148, 163, 184, .08)' },
                    ticks: { color: '#94a3b8' }
                },
                voc: {
                    type: 'linear',
                    position: 'right',
                    beginAtZero: true,
                    title: { display: true, text: 'VOC (ppm)', color: '#22d3ee' },
                    grid: { drawOnChartArea: false },
                    ticks: { color: '#94a3b8' }
                }
            }
        }
    });
}

elements.connectionButton.addEventListener('click', () => {
    if (client && client.connected) {
        disconnectMqtt();
    } else {
        if (client) {
            intentionalDisconnect = true;
            client.end(true);
            client = null;
        }
        connectMqtt();
    }
});

document.getElementById('clearChartButton').addEventListener('click', () => {
    if (!chart) return;
    chart.data.labels = [];
    chart.data.datasets.forEach(dataset => { dataset.data = []; });
    chart.update();
});

window.addEventListener('DOMContentLoaded', () => {
    initializeChart();
    connectMqtt();
});

window.setInterval(() => {
    if (lastTelemetryAt && Date.now() - lastTelemetryAt > STALE_AFTER_MS) {
        elements.deviceState.textContent = 'Estado del dispositivo: sin datos recientes';
        elements.deviceState.className = 'mt-4 text-xs text-amber-300';
    }
}, 1000);

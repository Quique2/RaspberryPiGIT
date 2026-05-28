# Digital Twin — Raspberry Pi IIoT Gateway
## Contexto completo para desarrollo de frontend/backend

> Este documento fue generado en la Raspberry Pi para que el equipo de desarrollo web entienda la arquitectura y estado actual del proyecto.

---

## Objetivo del proyecto

Implementar un **Digital Twin** de una celda de manufactura Schneider Electric con:
- **Lexium Cobot** (brazo robótico 6 ejes, fabricado por JAKA) — **LECTURA Y CONTROL funcionando**
- **PLC Schneider TM262-15** — objetivo futuro
- **Raspberry Pi 4** como gateway IIoT entre la red industrial y el backend

---

## Red y dispositivos

La RPi y el Cobot Controller son **dual-homed** (presentes en ambas subredes).
La salida a internet (túnel ngrok) sale por wlan0.

```
Internet
   │  wlan0 = 10.22.160.24/20  (WiFi institucional)
   │
Raspberry Pi 4 ── eth0 = 192.168.1.167  +  10.5.5.200  (dos IPs en eth0)
   │
   ├──────────── Subred 192.168.1.0/24 ────────────┐
   │                                                │
   │  192.168.1.1    Router / Gateway (HTTP)        │
   │  192.168.1.50   PLC Schneider TM262-15 (HTTP/HTTPS)
   │  192.168.1.167  Raspberry Pi (eth0)            │
   │  192.168.1.252  Cobot Controller (2ª NIC)      │
   │                                                │
   └──────────── Subred 10.5.5.0/24 ────────────────┘
      10.5.5.1    Router / Gateway (HTTP/HTTPS)
      10.5.5.100  Cobot Controller (NIC principal)
      10.5.5.101  PC EcoStruxure Cobot Expert (OPC-UA 4840)
      10.5.5.200  Raspberry Pi (eth0, IP secundaria)
```

### Tabla de IPs

**Subred 192.168.1.0/24 (red principal / PLC)**

| IP | Componente | Puertos |
|---|---|---|
| 192.168.1.1 | Router / Gateway | HTTP |
| 192.168.1.50 | PLC Schneider TM262-15 | HTTP, HTTPS |
| 192.168.1.167 | Raspberry Pi (eth0) | SSH, VNC, Backend :8000 |
| 192.168.1.252 | Cobot Controller (2ª NIC) | 6502, 10001, 2121 |

**Subred 10.5.5.0/24 (red del cobot)**

| IP | Componente | Puertos |
|---|---|---|
| 10.5.5.1 | Router / Gateway | HTTP, HTTPS |
| 10.5.5.100 | Cobot Controller (NIC principal) | 6502, 10001, 2121 |
| 10.5.5.101 | PC EcoStruxure Cobot Expert | OPC-UA 4840, HTTP |
| 10.5.5.200 | Raspberry Pi (eth0 secundaria) | SSH, VNC, Backend :8000 |

### Puertos del Cobot Controller (10.5.5.100)

```
6502   → Modbus TCP (lectura, FC04)
10000  → Estado JSON en tiempo real (TLS)
10001  → Comandos JSON (TLS) ← CONTROL (movimiento + gripper)
2121   → FTP (programas, requiere TLS)
```

Identificación del cobot (broadcast UDP): `LexiumCobot`, serial `8A24176JKA00007`, modelo `LXMRL03S0000`.

---

## Protocolo de control — DESCUBIERTO Y FUNCIONANDO

### Lectura de estado (puerto 10000 + TLS)

Conectar con TLS (sin verificar certificado) y leer un paquete JSON:

```python
import ssl, socket, json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

raw = socket.create_connection(('10.5.5.100', 10000), timeout=5)
s = ctx.wrap_socket(raw)
# Leer hasta tener JSON completo (~6KB por paquete)
buf = b''
while True:
    buf += s.recv(8192)
    try:
        data = json.loads(buf)
        break
    except: pass
```

**Campos del JSON de estado:**
```json
{
  "joint_position":     [-9.13, 87.75, -1.71, 108.98, 4.84, -9.51],
  "cartesian_position": [4.88, 8.43, 740.12, -88.74, 5.46, 175.66],
  "enabled":            true,
  "powered_on":         1,
  "error_code":         "0x0",
  "error_msg":          "",
  "drag_status":        false,
  "protective_stop":    0,
  "emergency_stop":     0,
  "in_position":        true,
  "tcp_speed":          0.0,
  "task_state":         4,
  "task_mode":          1,
  "speed_rate":         1.0,
  "collision_stop":     0,
  "paused":             false,
  "on_soft_limit":      0
}
```

### Envío de comandos (puerto 10001 + TLS)

```python
raw = socket.create_connection(('10.5.5.100', 10001), timeout=5)
s = ctx.wrap_socket(raw)
s.send((json.dumps(cmd) + '\n').encode())
resp = json.loads(s.recv(4096).decode())
# resp = {"errorCode": "0", "errorMsg": "", "cmdName": "..."}
```

**IMPORTANTE:** El cobot debe estar en modo **Remote Control** (Delegate Control activado en EcoStruxure Cobot Expert). Sin esto, los comandos devuelven `errorCode: "3" — permission deny`.

### Comandos disponibles (probados y funcionando)

**Verificar estado:**
```json
{"cmdName": "get_robot_state"}
```
Respuesta: `{"errorCode":"0","enable":"robot_enabled","power":"powered_on"}`

**Mover en espacio articular (MoveJ):**
```json
{
  "cmdName": "joint_move",
  "relFlag": 0,
  "jointPosition": [45.0, 87.75, -1.71, 108.98, 4.84, -9.51],
  "speed": 15.0,
  "accel": 15.0
}
```
- `relFlag: 0` = posición absoluta en grados
- `relFlag: 1` = movimiento relativo (delta en grados)
- `speed`: porcentaje de velocidad máxima (1-100)

**Mover en espacio cartesiano (MoveL):**
```json
{
  "cmdName": "moveL",
  "relFlag": 0,
  "cartPosition": [100.0, 200.0, 300.0, 0.0, 0.0, 0.0],
  "speed": 20.0,
  "accel": 50.0
}
```
- X/Y/Z en mm, RX/RY/RZ en grados

**Otros comandos:**
```json
{"cmdName": "power_on"}
{"cmdName": "power_off"}
{"cmdName": "enable_robot"}
{"cmdName": "disable_robot"}
{"cmdName": "stop_program"}
{"cmdName": "set_rapidrate", "rapidrate": 50.0}
```

---

## Archivos en el repo (Quique2/RaspberryPiGIT)

| Archivo | Descripción | Estado |
|---|---|---|
| `backend.py` | FastAPI con WebSocket + REST (lectura Modbus) | Funcionando |
| `cobot_reader.py` | Lector Modbus FC04 (posiciones, TCP, temps) | Funcionando |
| `cobot_control.py` | Control TLS JSON (mover joints, cartesiano, stop) | **NUEVO — Funcionando** |
| `start_gateway.sh` | Arranca backend + túnel ngrok | Funcionando |
| `modbus_check.py` | Verificador Modbus TCP | Listo |
| `network_scanner*.py` | Scanner de red industrial | Listo |

---

## Lo que falta agregar al backend FastAPI

El `backend.py` actual solo lee datos vía Modbus. Hay que agregar endpoints de control usando `cobot_control.py`.

### Endpoints a agregar:

```
POST /api/cobot/move/joint
  Body: { "joints": [j1,j2,j3,j4,j5,j6], "speed": 15, "relative": false }

POST /api/cobot/move/cartesian
  Body: { "x": 100, "y": 200, "z": 300, "rx": 0, "ry": 0, "rz": 0, "speed": 20 }

POST /api/cobot/stop
  Body: {}

POST /api/cobot/enable
POST /api/cobot/disable
```

### Integración en backend.py:

```python
from cobot_control import CobotController

ctrl = CobotController()
ctrl.connect()

@app.post("/api/cobot/move/joint")
async def move_joint(body: dict):
    result = ctrl.move_joint(body["joints"], speed=body.get("speed", 15))
    return {"ok": result.success, "error": result.error_msg}

@app.post("/api/cobot/stop")
async def stop():
    return {"ok": ctrl.stop().success}
```

### Para el stream de estado:
El WebSocket `/ws/cobot` ya existe y emite datos vía Modbus. Para tener AMBAS fuentes (Modbus + TLS estado), se puede usar el puerto 10000 TLS como fuente alternativa cuando se quiera el campo `cartesian_position` directamente (Modbus también lo tiene como `tcp_position`).

---

## Arquitectura completa actual

```
Browser (Railway HTTPS)
    │
    ├── GET  wss://unmoral-shrink-cavalry.ngrok-free.dev/ws/cobot
    │       → stream JSON cada 100ms (joint_positions_deg, tcp_position, status)
    │
    ├── GET  https://.../api/cobot/state  → snapshot
    │
    ├── POST https://.../api/cobot/move/joint     ← PENDIENTE en backend
    ├── POST https://.../api/cobot/move/cartesian ← PENDIENTE en backend
    └── POST https://.../api/cobot/stop           ← PENDIENTE en backend
         │
    FastAPI (RPi :8000)
         │
         ├── Lee Modbus FC04 cada 100ms → estado articular
         └── Envía comandos TLS JSON puerto 10001 → mueve el robot
              │
         Lexium Cobot (10.5.5.100)
              ├── Puerto 6502: Modbus (lectura)
              ├── Puerto 10000: estado TLS (JSON)
              └── Puerto 10001: comandos TLS (JSON)
```

---

## Precondición para control

**EcoStruxure Cobot Expert debe estar abierto en el PC (10.5.5.101) y en modo Remote Control.**

Pasos:
1. Abrir EcoStruxure Cobot Expert
2. Conectar al robot
3. Clic en "Remote Control" → Confirmar
4. La pantalla se oscurece — el robot acepta comandos externos

Sin esto: `errorCode: "3" — permission deny, please check remote control source`

---

## URLs públicas activas (ngrok estático)

```
WebSocket : wss://unmoral-shrink-cavalry.ngrok-free.dev/ws/cobot
REST      : https://unmoral-shrink-cavalry.ngrok-free.dev/api/cobot/state
```

Para activar: `cd ~/Modbus_TCP_Communication && ./start_gateway.sh`

---

*Última actualización: 2026-05-28 — Control de movimiento confirmado funcionando*
*Robot movido con éxito J1: 0° → 45° → 90° desde Raspberry Pi vía TLS JSON*

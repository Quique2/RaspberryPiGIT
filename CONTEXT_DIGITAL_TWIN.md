# Digital Twin — Raspberry Pi IIoT Gateway
## Contexto completo para desarrollo de frontend/backend

> Este documento fue generado en la Raspberry Pi para que el equipo de desarrollo web entienda la arquitectura y estado actual del proyecto.

---

## Objetivo del proyecto

Implementar un **Digital Twin** de una celda de manufactura Schneider Electric con:
- **Lexium Cobot** (brazo robótico 6 ejes) — conectado vía Modbus TCP
- **PLC Schneider TM262-15** — objetivo futuro (acceso bloqueado actualmente)
- **Raspberry Pi 4** como gateway IIoT entre la red industrial y el backend

El Digital Twin debe:
1. Monitorear en tiempo real el estado del cobot (posición, TCP, temperaturas, estado)
2. **Controlar el cobot desde la web** usando el modo Remote Control (Opción B elegida)
3. Visualizar en 3D la posición actual del brazo robótico

---

## Red y dispositivos

```
Internet / WiFi (wlan0)
        │
  Raspberry Pi 4 (192.168.1.167 / wlan0)
        │
   Ethernet (eth0, 10.5.5.200/24 — IP secundaria añadida manualmente)
        │
   ┌────┴─────────────────────────┐
   │                              │
Lexium Cobot Controller      PC Cobot Expert
10.5.5.100                   10.5.5.101
Puerto Modbus TCP: 6502      Puerto OPC UA: 4840
```

- **Raspberry Pi OS** ARM64, Python 3.13.5
- **pymodbus 3.13.0** instalado
- **NetworkManager** (no dhcpcd) gestiona la red
- eth0 configurado con `ipv4.never-default yes` para que wlan0 siempre sea la ruta por defecto (evita perder VNC)

---

## Estado actual — Lo que YA FUNCIONA

### Lectura Modbus TCP del Cobot (COMPLETO)

El script `cobot_reader.py` lee todos los datos del cobot en tiempo real:

```bash
python3 cobot_reader.py                        # snapshot único
python3 cobot_reader.py --loop --interval 1    # polling continuo
python3 cobot_reader.py --loop --save cobot_live.json  # guarda JSON
```

**Ejemplo de salida real:**
```
Power ON          : YES
Robot enabled     : NO  (standby)
Motion mode       : Jog/Other (0)
In position       : YES
Error code        : 3182721 (normal en standby)
Controlador temp  : 29.0 °C

J1:  60.439°   33°C   0.0A
J2:  81.909°   34°C   0.0A
J3:   7.191°   32°C   0.0A
J4:  87.090°   35°C   0.0A
J5:   7.354°   36°C   0.0A
J6: -77.118°   38°C   0.0A

TCP: X=20.96mm  Y=56.38mm  Z=738.96mm
     RX=-93.077°  RY=-80.883°  RZ=-109.185°
```

---

## Arquitectura objetivo

```
┌─────────────────────────────────────────────┐
│              Página Web (Browser)            │
│  - 3D viewer del cobot (Three.js / Babylon) │
│  - Panel de control: mover a posición XYZ   │
│  - Monitor en tiempo real: joints, TCP       │
│  - Estado: power, errores, temperatura       │
└──────────────┬──────────────────────────────┘
               │ HTTP REST + WebSocket
┌──────────────▼──────────────────────────────┐
│         Backend (en Raspberry Pi)            │
│  - Framework: FastAPI (Python) recomendado   │
│  - WebSocket → streaming datos cobot         │
│  - REST API → enviar comandos de control     │
│  - Lee Modbus FC04 cada ~100ms               │
│  - Escribe comandos vía LexiumCobot SDK      │
└──────────────┬──────────────────────────────┘
               │ Modbus TCP (puerto 6502)
               │ LexiumCobotCommunication SDK
┌──────────────▼──────────────────────────────┐
│      Lexium Cobot Controller (10.5.5.100)    │
│  - Modo: Remote Control (Delegate Control)   │
│  - Lee: posiciones, TCP, estado, temp        │
│  - Escribe: comandos de movimiento           │
└─────────────────────────────────────────────┘
```

---

## Control del Cobot — Opción B (elegida)

### Qué es Delegate Control / Remote Control

El cobot tiene 3 fuentes de control (solo una activa a la vez):
1. **EcoStruxure Cobot Expert** (tablet/PC app)
2. **Remote** ← lo que queremos usar desde la web
3. **Control Stick** (hardware físico)

Para activar modo Remote en el cobot:
1. Abrir EcoStruxure Cobot Expert en el PC (10.5.5.101)
2. Clic en botón **Remote Control** en menú superior
3. Confirmar "Delegate control → Remote"
4. El cobot queda en modo Remote, esperando comandos externos

### LexiumCobotCommunication Library

Esta es la librería oficial de Schneider Electric para control remoto del cobot.

**Lo que permite:**
- Enviar posiciones cartesianas (X, Y, Z, RX, RY, RZ) — movimiento absoluto
- Control articular (J1–J6 en grados) — movimiento por joint
- Configurar velocidad y aceleración
- Ejecutar movimientos lineales y circulares
- Control de I/O digital y analógico
- Gestión de herramientas (tool/TCP offset)

**Importante:** Esta librería NO está instalada aún en la Raspberry Pi. Hay que investigar cómo obtenerla:
- Nombre: `LexiumCobotCommunication`
- Schneider lo distribuye como SDK
- Puede existir un wrapper Python o puede requerir C#/.NET
- Alternativa si el SDK no tiene Python: usar un servidor intermediario en el PC (10.5.5.101) que exponga una API REST y lo llame desde la RPi

**Tarea pendiente:** Investigar si existe binding Python de LexiumCobotCommunication o cómo integrarlo.

---

## Mapa de registros Modbus TCP del Cobot

**Puerto:** 6502  
**IP:** 10.5.5.100  
**Unit ID:** 1 (o 0, ambos funcionan)

### LECTURA (Function Code 04 — Read Input Registers)

Todos los datos del cobot son **solo lectura** (FC 04). Los Float32 son Big-Endian, 2 registros por valor.

#### Estado del robot (UINT16, 1 registro cada uno)
| Registro | Nombre | Valores |
|---|---|---|
| 454 | PROTECTIVE_STOP | 0=normal, 1=colisión detectada |
| 455 | EMERGENCY_STOP | 0=normal, 1=parada emergencia |
| 456 | POWER_ON | 0=apagado, 1=encendido |
| 457 | ROBOT_ENABLE | 0=deshabilitado, 1=habilitado |
| 458 | ON_SOFT_LIMIT | 0=normal, 1=en límite software |
| 459 | INPOS | 0=en movimiento, 1=en posición |
| 460 | Motion mode | 0=Jog, 1=Hand-guided, 2=Admittance, 4=Servo |
| 461 | Reduction level | 1=first, 2=second, 3=protective stop |

#### Posiciones articulares (Float32 Big-Endian, 2 registros por eje)
| Registros | Datos | Unidad |
|---|---|---|
| 382–383 | Joint 1 position | ° |
| 384–385 | Joint 2 position | ° |
| 386–387 | Joint 3 position | ° |
| 388–389 | Joint 4 position | ° |
| 390–391 | Joint 5 position | ° |
| 392–393 | Joint 6 position | ° |
| 394–395 | Joint 1 speed | °/s |
| 396–397 | Joint 2 speed | °/s |
| 398–399 | Joint 3 speed | °/s |
| 400–401 | Joint 4 speed | °/s |
| 402–403 | Joint 5 speed | °/s |
| 404–405 | Joint 6 speed | °/s |

#### TCP — Tool Center Point (Float32, 2 registros)
| Registros | Datos | Unidad |
|---|---|---|
| 406–407 | TCP position X | mm |
| 408–409 | TCP position Y | mm |
| 410–411 | TCP position Z | mm |
| 412–413 | TCP position RX | ° |
| 414–415 | TCP position RY | ° |
| 416–417 | TCP position RZ | ° |
| 418–419 | TCP speed X | mm/s |
| 420–421 | TCP speed Y | mm/s |
| 422–423 | TCP speed Z | mm/s |

#### Salud del robot (Float32)
| Registros | Datos | Unidad |
|---|---|---|
| 462–463 | Speed magnification | % |
| 464–465 | MOTION_ERRCODE | INT32 |
| 466–467 | Controller temperature | °C |
| 468–469 | Controller avg power | W |
| 470–471 | Controller avg current | A |

#### Por articulación (Float32)
| Registros | Datos | Unidad |
|---|---|---|
| 316–317 | Joint 1 temperature | °C |
| 318–319 | Joint 2 temperature | °C |
| 320–321 | Joint 3 temperature | °C |
| 322–323 | Joint 4 temperature | °C |
| 324–325 | Joint 5 temperature | °C |
| 326–327 | Joint 6 temperature | °C |
| 358–359 | Joint 1 current | A |
| 360–361 | Joint 2 current | A |
| 362–363 | Joint 3 current | A |
| 364–365 | Joint 4 current | A |
| 366–367 | Joint 5 current | A |
| 368–369 | Joint 6 current | A |
| 370–371 | Sensor force X | N |
| 372–373 | Sensor force Y | N |
| 374–375 | Sensor force Z | N |
| 376–377 | Sensor torque RX | Nm |
| 378–379 | Sensor torque RY | Nm |
| 380–381 | Sensor torque RZ | Nm |

#### Estados por articulación (UINT16, 1 registro)
| Registros | Datos |
|---|---|
| 340–345 | Joint 1–6 error state (0=ok, 1=error) |
| 346–351 | Joint 1–6 enable state (0=off, 1=on) |
| 352–357 | Joint 1–6 collision state (0=ok, 1=collision) |

### ESCRITURA (Function Code 03/06 — Holding Registers)
Los registros AI00–AI63 (addresses 100–195) son **Holding Registers escribibles** — sirven para I/O general, no para comandos de movimiento directo.

---

## Formato JSON del cobot_reader.py

```json
{
  "timestamp": "2026-05-27T20:54:40.039330",
  "ok": true,
  "status": {
    "protective_stop": false,
    "emergency_stop": false,
    "power_on": true,
    "robot_enabled": false,
    "on_soft_limit": false,
    "inpos": true,
    "motion_mode": 0,
    "motion_mode_name": "Jog/Other",
    "reduction_level": 0,
    "speed_magnification_pct": 1.0,
    "motion_errcode": 3182721
  },
  "controller": {
    "temperature_c": 29.0,
    "avg_power_w": 0.0,
    "avg_current_a": 0.0
  },
  "joint_states": [
    {"joint": 1, "error": false, "enabled": false, "collision": false, "current_a": 0.0},
    ...
  ],
  "joint_positions_deg": [60.439, 81.909, 7.191, 87.090, 7.354, -77.118],
  "joint_speeds_deg_s": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  "tcp_position": {
    "x_mm": 20.96, "y_mm": 56.38, "z_mm": 738.96,
    "rx_deg": -93.077, "ry_deg": -80.883, "rz_deg": -109.185
  },
  "tcp_speed": {...},
  "end_effector": {
    "fx_n": 0.0, "fy_n": 0.0, "fz_n": 0.0,
    "torque_rx_nm": 0.0, "torque_ry_nm": 0.0, "torque_rz_nm": 0.0
  },
  "joint_temperatures_c": [33, 34, 32, 35, 36, 38]
}
```

---

## API REST propuesta para el backend (FastAPI)

```
GET  /api/cobot/state          → JSON con estado completo (snapshot)
WS   /ws/cobot                 → WebSocket, emite estado cada 100ms
POST /api/cobot/move/joint     → { joints: [j1,j2,j3,j4,j5,j6], speed: 50 }
POST /api/cobot/move/cartesian → { x, y, z, rx, ry, rz, speed }
POST /api/cobot/move/home      → mover a posición home
POST /api/cobot/enable         → habilitar robot
POST /api/cobot/disable        → deshabilitar robot
POST /api/cobot/stop           → parada inmediata
GET  /api/cobot/registers      → valores raw de registros Modbus
```

---

## Archivos en este repositorio

| Archivo | Descripción |
|---|---|
| `cobot_reader.py` | Lector completo de datos del cobot vía Modbus FC04 |
| `modbus_check.py` | Verificador de conectividad Modbus TCP |
| `network_scanner.py` | Scanner de red industrial (ping + TCP + ARP) |
| `network_scanner_arp.py` | Scanner con ARP broadcast (requiere sudo + scapy) |
| `INSTALACION_RASP_DIGITAL_TWIN.md` | Guía de instalación de dependencias en la RPi |
| `CONTEXT_DIGITAL_TWIN.md` | Este archivo — contexto completo del proyecto |

---

## Dependencias instaladas en la Raspberry Pi

```bash
pip3 install pymodbus --break-system-packages        # v3.13.0 ✓
pip3 install asyncua --break-system-packages         # OPC UA async
pip3 install opcua --break-system-packages           # OPC UA sync
pip3 install netifaces --break-system-packages       # detección red
npm install node-opcua                               # OPC UA Node.js
```

> Nota: Se usa `--break-system-packages` porque Raspberry Pi OS usa PEP 668

---

## OPC UA (estado: pendiente)

El PC en 10.5.5.101 tiene un servidor OPC UA en puerto 4840. La conexión TCP funciona pero el servidor rechaza la sesión porque requiere que el certificado del cliente esté en su lista de confianza.

- Certificado generado: `raspberry_pi_opcua_cert.pem` (CN=NodeOPCUA-Client@bitsperbox-demo)
- Pendiente: importar ese certificado en Machine Expert Twin / servidor OPC UA

---

## Próximos pasos

1. **[PRIORITARIO]** Investigar LexiumCobotCommunication SDK — ¿tiene binding Python?
2. Crear backend FastAPI en RPi con WebSocket para streaming de datos
3. Conectar frontend 3D (Three.js/Babylon.js) a WebSocket del RPi
4. Implementar endpoints de control (move_joint, move_cartesian, stop)
5. Activar Delegate Control en Cobot Expert para habilitar modo Remote
6. Resolver OPC UA (importar certificado RPi en Machine Expert Twin)
7. Conectar al PLC TM262-15 cuando se resuelva el acceso (credenciales fredfactory)

---

## Modelo del cobot

**Lexium Cobot** — serie LXM (probablemente LXM15 o LXM18 según lo visto en Cobot Expert)
- 6 ejes de rotación
- Rangos de movimiento aprox: J1±360°, J2±360°, J3±225°, J4±360°, J5±115°, J6±360°
- Controlador: Lexium Cobot Controller (IP: 10.5.5.100)
- Comunicación: Modbus TCP (6502), Profinet, EtherNet/IP, OPC UA

---

*Última actualización: 2026-05-27*  
*Generado desde: Raspberry Pi 4 (bitsperbox-demo) — admin1@bitsperbox-demo*

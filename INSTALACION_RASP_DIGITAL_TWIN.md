# Instalación RaspDigitalTwin

Documentación de la instalación realizada el 2026-05-26.

## Fuente

Repositorio: https://github.com/a01769810-svg/RaspDigitalTwin

Se descargó y descomprimió el archivo `schneider-rpi-control.zip` que contiene el plan técnico y las skills de Claude Code.

---

## Dependencias del sistema instaladas

```bash
sudo apt install -y python3-venv python3-pip git swig liblgpio-dev python3-lgpio
```

---

## Entorno virtual Python

Ubicación: `~/schneider-rpi-control/.venv`

```bash
mkdir -p ~/schneider-rpi-control
cd ~/schneider-rpi-control
python3 -m venv .venv
source .venv/bin/activate
```

---

## Librerías Python instaladas

```bash
pip install gpiozero lgpio rpi-lgpio pymodbus fastapi "uvicorn[standard]" websockets pydantic python-dotenv pigpio
```

### Lista completa (27 paquetes)

| Paquete | Versión | Descripción |
|---------|---------|-------------|
| gpiozero | 2.0.1 | Control GPIO de alto nivel |
| lgpio | 0.2.2.0 | Backend GPIO para Raspberry Pi 4/5 |
| rpi-lgpio | 0.6 | Capa de compatibilidad con RPi.GPIO |
| pymodbus | 3.13.0 | Cliente/servidor Modbus TCP |
| fastapi | 0.136.3 | Framework web para API REST y WebSocket |
| uvicorn | 0.48.0 | Servidor ASGI para FastAPI |
| websockets | 16.0 | Librería WebSocket |
| pydantic | 2.13.4 | Validación de datos con type hints |
| python-dotenv | 1.2.2 | Carga variables desde archivo .env |
| pigpio | 1.78 | Control GPIO con timing preciso (opcional) |

---

## Skills de Claude Code

Se copiaron 6 skills a `~/.claude/skills/` para asistencia especializada:

| Skill | Uso |
|-------|-----|
| `stepper-nema-control` | Código para motor NEMA stepper (GPIO18=STEP, GPIO23=DIR) |
| `solenoid-mosfet-control` | Código para solenoide via MOSFET (GPIO25) |
| `modbus-tcp-gateway` | Comunicación Modbus TCP con PLC Schneider |
| `digital-twin-websocket` | Gateway FastAPI/WebSocket al digital twin web |
| `rpi-gpio-safety-audit` | Auditoría de seguridad de cableado GPIO |
| `rpi-release-check` | Verificaciones antes de commit/entrega |

---

## Pinout GPIO (BCM)

| Función | GPIO | Pin físico |
|---------|------|------------|
| STEP/PUL NEMA | GPIO18 | 12 |
| DIR NEMA | GPIO23 | 16 |
| Solenoide (MOSFET) | GPIO25 | 22 |

---

## Estructura del proyecto

```
~/schneider-rpi-control/
├── .claude/
│   └── skills/
│       ├── stepper-nema-control/SKILL.md
│       ├── solenoid-mosfet-control/SKILL.md
│       ├── modbus-tcp-gateway/SKILL.md
│       ├── digital-twin-websocket/SKILL.md
│       ├── rpi-gpio-safety-audit/SKILL.md
│       └── rpi-release-check/SKILL.md
├── .venv/                    # Entorno virtual Python
├── RPI_CONTROL_PLAN.md       # Plan técnico completo
└── README.md
```

---

## Cómo usar

### Activar entorno virtual

```bash
cd ~/schneider-rpi-control
source .venv/bin/activate
```

### Verificar instalación

```bash
python -c "import gpiozero; import pymodbus; import fastapi; print('OK')"
```

---

## Próximos pasos (según RPI_CONTROL_PLAN.md)

1. Probar GPIO con LEDs (sin cargas)
2. Implementar `stepper_controller.py`
3. Implementar `solenoid_controller.py`
4. Levantar `web_gateway.py` con datos mock
5. Integrar `modbus_client.py` con simulador
6. Conectar al digital twin web real

---

## Notas importantes

- La Raspberry Pi es un **controlador auxiliar**, NO un safety controller
- El PLC Schneider mantiene las funciones de seguridad críticas
- Nunca alimentar cargas directamente desde GPIO (usar fuente externa)
- El solenoide requiere MOSFET + diodo flyback
- Verificar que el driver NEMA acepta lógica de 3.3V antes de conectar

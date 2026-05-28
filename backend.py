#!/usr/bin/env python3
"""
Digital Twin — FastAPI Backend
Raspberry Pi IIoT Gateway

Exposes live Lexium Cobot data over:
  - WebSocket  ws://RPi_IP:8000/ws/cobot   (100 ms stream)
  - REST GET   http://RPi_IP:8000/api/cobot/state

The JSON format matches exactly what cobot_reader.py produces,
which is what the frontend (SchneiderProjectWeb_DigitalTwin) expects.

Run:
    python3 backend.py
    uvicorn backend:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from cobot_reader import read_cobot, HOST, PORT, TIMEOUT

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    ModbusTcpClient = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL   = 0.1   # 100 ms
RECONNECT_DELAY = 3.0   # seconds between Modbus reconnect attempts

# ---------------------------------------------------------------------------
# Shared state — one Modbus client, one latest snapshot, many WS subscribers
# ---------------------------------------------------------------------------
class CobotState:
    def __init__(self):
        self.latest: dict = _demo_snapshot()
        self.client: ModbusTcpClient | None = None
        self.connected: bool = False
        self.subscribers: list[asyncio.Queue] = []

    def broadcast(self, data: dict) -> None:
        self.latest = data
        dead = []
        for q in self.subscribers:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.subscribers.remove(q)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)


def _demo_snapshot() -> dict:
    """Real snapshot captured from the cobot — used when Modbus is unavailable."""
    return {
        "timestamp": datetime.now().isoformat(),
        "ok": True,
        "_demo": True,
        "status": {
            "protective_stop": False,
            "emergency_stop": False,
            "power_on": True,
            "robot_enabled": False,
            "on_soft_limit": False,
            "inpos": True,
            "motion_mode": 0,
            "motion_mode_name": "Jog/Other",
            "reduction_level": 0,
            "speed_magnification_pct": 1.0,
            "motion_errcode": 3182721,
        },
        "controller": {
            "temperature_c": 29.0,
            "avg_power_w": 0.0,
            "avg_current_a": 0.0,
        },
        "joint_states": [
            {"joint": j, "error": False, "enabled": False, "collision": False, "current_a": 0.0}
            for j in range(1, 7)
        ],
        "joint_positions_deg": [60.439, 81.909, 7.191, 87.090, 7.354, -77.118],
        "joint_speeds_deg_s":  [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "tcp_position": {
            "x_mm": 20.96, "y_mm": 56.38, "z_mm": 738.96,
            "rx_deg": -93.077, "ry_deg": -80.883, "rz_deg": -109.185,
        },
        "tcp_speed": {
            "vx_mm_s": 0.0, "vy_mm_s": 0.0, "vz_mm_s": 0.0,
            "vrx_deg_s": 0.0, "vry_deg_s": 0.0, "vrz_deg_s": 0.0,
        },
        "end_effector": {
            "fx_n": 0.0, "fy_n": 0.0, "fz_n": 0.0,
            "torque_rx_nm": 0.0, "torque_ry_nm": 0.0, "torque_rz_nm": 0.0,
        },
        "joint_temperatures_c": [33, 34, 32, 35, 36, 38],
    }


state = CobotState()


# ---------------------------------------------------------------------------
# Background polling loop
# ---------------------------------------------------------------------------
async def modbus_poll_loop() -> None:
    """Connect to cobot, poll every 100 ms, broadcast to all WebSocket clients."""
    while True:
        if ModbusTcpClient is None:
            log.warning("pymodbus not installed — broadcasting demo data.")
            while True:
                snap = _demo_snapshot()
                state.broadcast(snap)
                await asyncio.sleep(POLL_INTERVAL)

        log.info("Connecting to Lexium Cobot at %s:%d ...", HOST, PORT)
        client = ModbusTcpClient(HOST, port=PORT, timeout=TIMEOUT)

        if not client.connect():
            log.warning("Modbus connect failed — retrying in %.0fs, using demo data.", RECONNECT_DELAY)
            state.connected = False
            await asyncio.sleep(RECONNECT_DELAY)
            continue

        state.client = client
        state.connected = True
        log.info("Modbus connected. Streaming at %.0f ms.", POLL_INTERVAL * 1000)

        try:
            while True:
                snap = await asyncio.get_event_loop().run_in_executor(None, read_cobot, client)
                snap["timestamp"] = datetime.now().isoformat()
                state.broadcast(snap)
                await asyncio.sleep(POLL_INTERVAL)

        except Exception as exc:
            log.warning("Modbus error: %s — reconnecting.", exc)
            state.connected = False
        finally:
            client.close()
            state.client = None

        await asyncio.sleep(RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# App lifespan — start background task
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(modbus_poll_loop())
    yield
    task.cancel()


app = FastAPI(title="Digital Twin — Cobot Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------
@app.get("/api/cobot/state")
async def get_cobot_state() -> dict:
    return state.latest


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "modbus_connected": state.connected,
        "subscribers": len(state.subscribers),
        "timestamp": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/cobot")
async def ws_cobot(websocket: WebSocket) -> None:
    await websocket.accept()
    q = state.subscribe()
    log.info("WebSocket client connected. Total: %d", len(state.subscribers))

    try:
        # Send current snapshot immediately on connect
        await websocket.send_text(json.dumps(state.latest))

        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=0.5)
                await websocket.send_text(json.dumps(data))
            except asyncio.TimeoutError:
                # Send a heartbeat if no new data
                pass

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("WebSocket error: %s", exc)
    finally:
        state.unsubscribe(q)
        log.info("WebSocket client disconnected. Total: %d", len(state.subscribers))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import socket
    local_ip = socket.gethostbyname(socket.gethostname())
    print(f"\n  Digital Twin Backend")
    print(f"  ─────────────────────────────────────────")
    print(f"  WebSocket : ws://{local_ip}:8000/ws/cobot")
    print(f"  REST      : http://{local_ip}:8000/api/cobot/state")
    print(f"  Health    : http://{local_ip}:8000/health")
    print(f"  Cobot     : {HOST}:{PORT}")
    print(f"  ─────────────────────────────────────────\n")

    uvicorn.run("backend:app", host="0.0.0.0", port=8000, log_level="info")

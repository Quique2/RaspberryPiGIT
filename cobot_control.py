#!/usr/bin/env python3
"""
Lexium Cobot (JAKA) — Control Module
Raspberry Pi Gateway — Digital Twin Phase 2

Sends motion commands via JAKA TCP/JSON protocol:
  Port 10001 → send commands (JSON)
  Port 10000 → receive status stream (JSON)

IMPORTANT: EcoStruxure Cobot Expert must be open on the PC
and Delegate Control must be set to Remote mode before
any command will be accepted by the robot.

Reference: https://www.jaka.com/docs/en/guide/V3/tcpip.html
"""

import json
import socket
import ssl
import threading
import time
import logging
from dataclasses import dataclass

HOST = "10.5.5.100"
CMD_PORT    = 10001  # send commands here (TLS)
STATUS_PORT = 10000  # robot streams status here (TLS)
TIMEOUT     = 5.0

# Magnetic gripper wired to cabinet CN2 DO6 → dout index 5 (0-based)
GRIPPER_INDEX   = 5
GRIPPER_IO_TYPE = 0   # 0 = cabinet

def _tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

log = logging.getLogger(__name__)


@dataclass
class MoveResult:
    success: bool
    error_code: int = 0
    error_msg: str = ""


class CobotController:
    """
    JSON-TCP command interface to the Lexium Cobot (JAKA backend).

    Usage:
        ctrl = CobotController()
        ctrl.connect()
        ctrl.enable_robot()
        ctrl.move_joint([60, 80, 10, 85, 10, -75], speed=20)
        ctrl.disconnect()
    """

    def __init__(self, host: str = HOST, cmd_port: int = CMD_PORT):
        self.host = host
        self.cmd_port = cmd_port
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self.last_gripper_closed: bool = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        try:
            raw = socket.create_connection((self.host, self.cmd_port), timeout=TIMEOUT)
            s = _tls_context().wrap_socket(raw, server_hostname=self.host)
            self._sock = s
            log.info("Connected to cobot command port %s:%d", self.host, self.cmd_port)
            return True
        except Exception as exc:
            log.error("Connect failed: %s", exc)
            return False

    def disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def is_connected(self) -> bool:
        return self._sock is not None

    # ------------------------------------------------------------------
    # Low-level send/receive
    # ------------------------------------------------------------------
    def _send(self, cmd: dict) -> dict | None:
        if not self._sock:
            log.error("Not connected")
            return None
        with self._lock:
            try:
                payload = json.dumps(cmd) + "\n"
                self._sock.send(payload.encode())
                raw = self._sock.recv(4096)
                if raw:
                    return json.loads(raw.decode().strip())
            except json.JSONDecodeError as exc:
                log.warning("JSON parse error: %s", exc)
            except Exception as exc:
                log.error("Send error: %s", exc)
                self._sock = None
        return None

    def _ok(self, resp: dict | None) -> MoveResult:
        if resp is None:
            return MoveResult(False, -1, "No response from robot")
        code = int(resp.get("errorCode", -1))
        msg  = resp.get("errorMsg", "")
        return MoveResult(code == 0, code, msg)

    # ------------------------------------------------------------------
    # Power & enable
    # ------------------------------------------------------------------
    def power_on(self) -> MoveResult:
        resp = self._send({"cmdName": "power_on"})
        result = self._ok(resp)
        if result.success:
            log.info("Robot powered ON")
        return result

    def power_off(self) -> MoveResult:
        return self._ok(self._send({"cmdName": "power_off"}))

    def enable_robot(self) -> MoveResult:
        resp = self._send({"cmdName": "enable_robot"})
        result = self._ok(resp)
        if result.success:
            log.info("Robot ENABLED")
        return result

    def disable_robot(self) -> MoveResult:
        return self._ok(self._send({"cmdName": "disable_robot"}))

    # ------------------------------------------------------------------
    # Motion — SAFE, checks before moving
    # ------------------------------------------------------------------
    def stop(self) -> MoveResult:
        """Immediate motion stop."""
        return self._ok(self._send({"cmdName": "stop_program"}))

    def move_home(self, speed: float = 15) -> MoveResult:
        """Move to home position [0, 0, 0, 0, 0, 0] in joint space."""
        return self.move_joint([0.0, 0.0, 0.0, 0.0, 0.0, 0.0], speed=speed)

    def move_joint(
        self,
        joints: list[float],
        speed: float = 20.0,
        accel: float = 20.0,
        relative: bool = False,
    ) -> MoveResult:
        """
        Joint-space move (MoveJ).
        joints: [J1, J2, J3, J4, J5, J6] in degrees
        speed:  percentage of max speed (1-100)
        """
        if len(joints) != 6:
            return MoveResult(False, -1, "joints must have 6 values")
        speed = max(1.0, min(100.0, speed))
        cmd = {
            "cmdName":       "joint_move",
            "relFlag":       1 if relative else 0,
            "jointPosition": joints,
            "speed":         speed,
            "accel":         accel,
        }
        resp = self._send(cmd)
        result = self._ok(resp)
        if result.success:
            log.info("MoveJ → %s  speed=%s%%", joints, speed)
        else:
            log.warning("MoveJ failed: [%d] %s", result.error_code, result.error_msg)
        return result

    def move_cartesian(
        self,
        x: float, y: float, z: float,
        rx: float, ry: float, rz: float,
        speed: float = 20.0,
        accel: float = 50.0,
        relative: bool = False,
    ) -> MoveResult:
        """
        Cartesian linear move (MoveL).
        x/y/z in mm, rx/ry/rz in degrees.
        speed: mm/s
        """
        speed = max(1.0, min(3000.0, speed))
        cmd = {
            "cmdName":      "moveL",
            "relFlag":      1 if relative else 0,
            "cartPosition": [x, y, z, rx, ry, rz],
            "speed":        speed,
            "accel":        accel,
        }
        resp = self._send(cmd)
        result = self._ok(resp)
        if result.success:
            log.info("MoveL → X%.1f Y%.1f Z%.1f  speed=%.1fmm/s", x, y, z, speed)
        else:
            log.warning("MoveL failed: [%d] %s", result.error_code, result.error_msg)
        return result

    def set_speed(self, percent: float) -> MoveResult:
        """Set global speed override (1-100%)."""
        percent = max(1.0, min(100.0, percent))
        return self._ok(self._send({"cmdName": "set_rapidrate", "rapidrate": percent}))

    # ------------------------------------------------------------------
    # Digital output / gripper
    # ------------------------------------------------------------------
    def set_digital_output(self, index: int, value: bool, io_type: int = 0) -> MoveResult:
        """
        Set a digital output.
        io_type: 0=cabinet, 1=tool end, 2=extend
        index:   0-based channel (DO6 on cabinet = index 5)
        """
        cmd = {
            "cmdName": "set_digital_output",
            "type":    io_type,
            "index":   index,
            "value":   1 if value else 0,
        }
        result = self._ok(self._send(cmd))
        if result.success:
            log.info("DO[type=%d,idx=%d] = %d", io_type, index, int(value))
        else:
            log.warning("set_digital_output failed: [%d] %s", result.error_code, result.error_msg)
        return result

    def set_gripper(self, closed: bool) -> MoveResult:
        """
        Magnetic gripper on cabinet DO6 (index 5).
        closed=True energizes the magnet (grabs), False releases.
        """
        result = self.set_digital_output(GRIPPER_INDEX, closed, io_type=GRIPPER_IO_TYPE)
        if result.success:
            self.last_gripper_closed = closed
        return result


# ---------------------------------------------------------------------------
# Quick test — run directly to verify connection
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ctrl = CobotController()
    print(f"\n  Conectando a {HOST}:{CMD_PORT} ...")

    if not ctrl.connect():
        print("  ERROR: No se pudo conectar.")
        print("  Verifica que EcoStruxure Cobot Expert esté abierto y en modo Remote.")
        raise SystemExit(1)

    print("  Conectado. Probando get_state ...")
    resp = ctrl._send({"cmdName": "get_robot_state"})
    print(f"  Estado: {resp}")

    ctrl.disconnect()
    print("  Listo.\n")

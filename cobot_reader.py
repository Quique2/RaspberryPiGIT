#!/usr/bin/env python3
"""
Lexium Cobot — Digital Twin Data Reader
Raspberry Pi Gateway — Digital Twin Phase 1

Reads live robot state via Modbus TCP (port 6502).
All Lexium Cobot data uses Function Code 04 (Read Input Registers).
Float32 values are Big-Endian across 2 consecutive registers.

Usage:
    python3 cobot_reader.py                        # single snapshot
    python3 cobot_reader.py --loop --interval 1    # continuous poll
    python3 cobot_reader.py --host 10.5.5.100 --port 6502
"""

import argparse
import json
import struct
import sys
import time
from datetime import datetime

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    print("ERROR: pymodbus not installed. Run: pip3 install pymodbus --break-system-packages")
    sys.exit(1)

HOST    = "10.5.5.100"
PORT    = 6502
UNIT_ID = 1
TIMEOUT = 3

# ---------------------------------------------------------------------------
# Float32 decode — Big-Endian, two 16-bit registers
# ---------------------------------------------------------------------------
def to_float32(reg_high: int, reg_low: int) -> float:
    raw = struct.pack(">HH", reg_high, reg_low)
    return struct.unpack(">f", raw)[0]

def to_int32(reg_high: int, reg_low: int) -> int:
    raw = struct.pack(">HH", reg_high, reg_low)
    return struct.unpack(">i", raw)[0]

# ---------------------------------------------------------------------------
# Read a block of input registers (FC 04)
# ---------------------------------------------------------------------------
def read_input(client: ModbusTcpClient, address: int, count: int) -> list[int] | None:
    resp = client.read_input_registers(address=address, count=count)
    if resp.isError():
        return None
    return list(resp.registers)

# ---------------------------------------------------------------------------
# Read all cobot data
# ---------------------------------------------------------------------------
def read_cobot(client: ModbusTcpClient) -> dict:
    data = {"timestamp": datetime.now().isoformat(), "ok": False}

    # --- Robot status block (UINT16, single registers, 454–462) ---
    status_regs = read_input(client, address=454, count=9)
    if status_regs is None:
        data["error"] = "Could not read status registers"
        return data

    data["status"] = {
        "protective_stop":   bool(status_regs[0]),   # 454
        "emergency_stop":    bool(status_regs[1]),   # 455
        "power_on":          bool(status_regs[2]),   # 456
        "robot_enabled":     bool(status_regs[3]),   # 457
        "on_soft_limit":     bool(status_regs[4]),   # 458
        "inpos":             bool(status_regs[5]),   # 459 — reached target
        "motion_mode":       status_regs[6],         # 460: 0=Jog,1=Hand,2=Adm,4=Servo
        "reduction_level":   status_regs[7],         # 461: 1/2/3
        # 462-463 = speed magnification (float32) — skip in this block
    }

    motion_mode_names = {0: "Jog/Other", 1: "Hand-guided", 2: "Admittance", 4: "Servo position"}
    data["status"]["motion_mode_name"] = motion_mode_names.get(status_regs[6], "Unknown")

    # --- Speed magnification (FLOAT32, 462–463) ---
    speed_regs = read_input(client, address=462, count=2)
    if speed_regs:
        data["status"]["speed_magnification_pct"] = round(to_float32(speed_regs[0], speed_regs[1]), 2)

    # --- Error code (INT32, 464–465) ---
    err_regs = read_input(client, address=464, count=2)
    if err_regs:
        data["status"]["motion_errcode"] = to_int32(err_regs[0], err_regs[1])

    # --- Controller health (466–471, three FLOAT32 pairs) ---
    health_regs = read_input(client, address=466, count=6)
    if health_regs:
        data["controller"] = {
            "temperature_c":    round(to_float32(health_regs[0], health_regs[1]), 2),  # 466
            "avg_power_w":      round(to_float32(health_regs[2], health_regs[3]), 2),  # 468
            "avg_current_a":    round(to_float32(health_regs[4], health_regs[5]), 2),  # 470
        }

    # --- Joint error/enable/collision states (340–357, UINT16 each) ---
    joint_state_regs = read_input(client, address=340, count=18)
    if joint_state_regs:
        data["joint_states"] = []
        for j in range(6):
            data["joint_states"].append({
                "joint":      j + 1,
                "error":      bool(joint_state_regs[j]),       # 340–345
                "enabled":    bool(joint_state_regs[6 + j]),   # 346–351
                "collision":  bool(joint_state_regs[12 + j]),  # 352–357
            })

    # --- Joint currents (358–369, FLOAT32 pairs) ---
    curr_regs = read_input(client, address=358, count=12)
    if curr_regs and data.get("joint_states"):
        for j in range(6):
            val = to_float32(curr_regs[j * 2], curr_regs[j * 2 + 1])
            data["joint_states"][j]["current_a"] = round(val, 3)

    # --- Joint positions (382–393, FLOAT32 pairs, degrees) ---
    pos_regs = read_input(client, address=382, count=12)
    if pos_regs:
        data["joint_positions_deg"] = []
        for j in range(6):
            val = to_float32(pos_regs[j * 2], pos_regs[j * 2 + 1])
            data["joint_positions_deg"].append(round(val, 4))

    # --- Joint speeds (394–405, FLOAT32 pairs, deg/s) ---
    spd_regs = read_input(client, address=394, count=12)
    if spd_regs:
        data["joint_speeds_deg_s"] = []
        for j in range(6):
            val = to_float32(spd_regs[j * 2], spd_regs[j * 2 + 1])
            data["joint_speeds_deg_s"].append(round(val, 4))

    # --- TCP position (406–417, FLOAT32 pairs) ---
    # X,Y,Z in mm; RX,RY,RZ in degrees
    tcp_regs = read_input(client, address=406, count=12)
    if tcp_regs:
        labels = ["x_mm", "y_mm", "z_mm", "rx_deg", "ry_deg", "rz_deg"]
        data["tcp_position"] = {}
        for i, label in enumerate(labels):
            val = to_float32(tcp_regs[i * 2], tcp_regs[i * 2 + 1])
            data["tcp_position"][label] = round(val, 4)

    # --- TCP speeds (418–429, FLOAT32 pairs, mm/s and deg/s) ---
    tcpspd_regs = read_input(client, address=418, count=12)
    if tcpspd_regs:
        labels = ["vx_mm_s", "vy_mm_s", "vz_mm_s", "vrx_deg_s", "vry_deg_s", "vrz_deg_s"]
        data["tcp_speed"] = {}
        for i, label in enumerate(labels):
            val = to_float32(tcpspd_regs[i * 2], tcpspd_regs[i * 2 + 1])
            data["tcp_speed"][label] = round(val, 4)

    # --- Sensor force/torque (370–381, FLOAT32 pairs) ---
    force_regs = read_input(client, address=370, count=12)
    if force_regs:
        labels = ["fx_n", "fy_n", "fz_n", "torque_rx_nm", "torque_ry_nm", "torque_rz_nm"]
        data["end_effector"] = {}
        for i, label in enumerate(labels):
            val = to_float32(force_regs[i * 2], force_regs[i * 2 + 1])
            data["end_effector"][label] = round(val, 4)

    # --- Joint temperatures (316–327, INT32 pairs, °C) ---
    temp_regs = read_input(client, address=316, count=12)
    if temp_regs:
        data["joint_temperatures_c"] = []
        for j in range(6):
            val = to_int32(temp_regs[j * 2], temp_regs[j * 2 + 1])
            data["joint_temperatures_c"].append(val)

    data["ok"] = True
    return data


# ---------------------------------------------------------------------------
# Console printer
# ---------------------------------------------------------------------------
def print_snapshot(d: dict) -> None:
    print(f"\n{'='*62}")
    print(f"  Lexium Cobot Snapshot — {d['timestamp']}")
    print(f"{'='*62}")

    if not d.get("ok"):
        print(f"  ERROR: {d.get('error', 'unknown')}")
        return

    s = d.get("status", {})
    print(f"\n  STATUS")
    print(f"    Power ON          : {'YES' if s.get('power_on') else 'NO'}")
    print(f"    Robot enabled     : {'YES' if s.get('robot_enabled') else 'NO'}")
    print(f"    Motion mode       : {s.get('motion_mode_name')} ({s.get('motion_mode')})")
    print(f"    In position       : {'YES' if s.get('inpos') else 'NO'}")
    print(f"    Protective stop   : {'ACTIVE' if s.get('protective_stop') else 'clear'}")
    print(f"    Emergency stop    : {'ACTIVE' if s.get('emergency_stop') else 'clear'}")
    print(f"    Speed magnif.     : {s.get('speed_magnification_pct', 'N/A')} %")
    print(f"    Error code        : {s.get('motion_errcode', 0)}")

    c = d.get("controller", {})
    if c:
        print(f"\n  CONTROLLER")
        print(f"    Temperature       : {c['temperature_c']} °C")
        print(f"    Avg power         : {c['avg_power_w']} W")
        print(f"    Avg current       : {c['avg_current_a']} A")

    jp = d.get("joint_positions_deg")
    js = d.get("joint_states", [])
    jt = d.get("joint_temperatures_c")
    jcur = [st.get("current_a") for st in js]

    if jp:
        print(f"\n  JOINT POSITIONS (degrees)")
        for i, angle in enumerate(jp):
            j_state = js[i] if i < len(js) else {}
            err_flag = " [ERROR]" if j_state.get("error") else ""
            col_flag = " [COLLISION]" if j_state.get("collision") else ""
            temp_str = f"  {jt[i]}°C" if jt and i < len(jt) else ""
            cur_str  = f"  {jcur[i]}A" if jcur[i] is not None else ""
            print(f"    J{i+1}: {angle:8.3f}°{temp_str}{cur_str}{err_flag}{col_flag}")

    tcp = d.get("tcp_position")
    if tcp:
        print(f"\n  TCP POSITION (Tool Center Point)")
        print(f"    X={tcp['x_mm']:8.2f} mm   Y={tcp['y_mm']:8.2f} mm   Z={tcp['z_mm']:8.2f} mm")
        print(f"    RX={tcp['rx_deg']:7.3f}°    RY={tcp['ry_deg']:7.3f}°    RZ={tcp['rz_deg']:7.3f}°")

    ef = d.get("end_effector")
    if ef:
        fx, fy, fz = ef["fx_n"], ef["fy_n"], ef["fz_n"]
        if any(abs(v) > 0.01 for v in [fx, fy, fz]):
            print(f"\n  END EFFECTOR FORCE/TORQUE")
            print(f"    Fx={fx:.3f}N  Fy={fy:.3f}N  Fz={fz:.3f}N")
            print(f"    Tx={ef['torque_rx_nm']:.3f}Nm  Ty={ef['torque_ry_nm']:.3f}Nm  Tz={ef['torque_rz_nm']:.3f}Nm")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Lexium Cobot Digital Twin data reader")
    parser.add_argument("--host",     default=HOST,    help=f"Cobot controller IP (default: {HOST})")
    parser.add_argument("--port",     type=int, default=PORT, help=f"Modbus port (default: {PORT})")
    parser.add_argument("--unit",     type=int, default=UNIT_ID, help="Modbus unit ID")
    parser.add_argument("--loop",     action="store_true", help="Poll continuously")
    parser.add_argument("--interval", type=float, default=1.0, help="Poll interval in seconds (default: 1)")
    parser.add_argument("--json",     action="store_true", help="Print raw JSON instead of formatted output")
    parser.add_argument("--save",     help="Save each snapshot to this JSON file")
    args = parser.parse_args()

    print(f"\n  Connecting to Lexium Cobot at {args.host}:{args.port} ...")
    client = ModbusTcpClient(args.host, port=args.port, timeout=TIMEOUT)

    if not client.connect():
        print(f"  ERROR: Cannot connect to {args.host}:{args.port}")
        sys.exit(1)

    print(f"  Connected.\n")

    try:
        while True:
            snapshot = read_cobot(client)

            if args.json:
                print(json.dumps(snapshot, indent=2))
            else:
                print_snapshot(snapshot)

            if args.save:
                with open(args.save, "w") as f:
                    json.dump(snapshot, f, indent=2)

            if not args.loop:
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        client.close()

    sys.exit(0 if True else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Modbus TCP Connectivity Checker
Raspberry Pi Gateway — Digital Twin Phase 1

Confirms that a specific IP responds to a Modbus TCP read.
Uses pymodbus to send a real Modbus frame and read holding registers.

Usage:
    python3 modbus_check.py --host 192.168.1.10
    python3 modbus_check.py --host 192.168.1.10 --unit 1 --register 0 --count 10
"""

import argparse
import sys
import json
from datetime import datetime

try:
    from pymodbus.client import ModbusTcpClient
    HAS_PYMODBUS = True
except ImportError:
    HAS_PYMODBUS = False


def check_modbus(host: str, port: int = 502, unit: int = 1,
                 register: int = 0, count: int = 10) -> dict:
    """
    Connect to Modbus TCP device and read holding registers.
    Returns a result dict with status and register values.
    """
    result = {
        "host":     host,
        "port":     port,
        "unit_id":  unit,
        "register": register,
        "count":    count,
        "timestamp": datetime.now().isoformat(),
        "connected":  False,
        "read_ok":    False,
        "values":     [],
        "error":      None,
    }

    client = ModbusTcpClient(host, port=port, timeout=3)

    try:
        if not client.connect():
            result["error"] = "TCP connection refused"
            return result

        result["connected"] = True
        response = client.read_holding_registers(address=register, count=count)

        if response.isError():
            result["error"] = f"Modbus error: {response}"
        else:
            result["read_ok"] = True
            result["values"]  = list(response.registers)

    except Exception as exc:
        result["error"] = str(exc)
    finally:
        client.close()

    return result


def main() -> None:
    if not HAS_PYMODBUS:
        print("ERROR: pymodbus not installed.")
        print("Install: pip3 install pymodbus")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Modbus TCP connectivity check for Schneider PLC"
    )
    parser.add_argument("--host",     required=True,       help="PLC IP address")
    parser.add_argument("--port",     type=int, default=502, help="Modbus port (default 502)")
    parser.add_argument("--unit",     type=int, default=1,   help="Modbus unit/slave ID (default 1)")
    parser.add_argument("--register", type=int, default=0,   help="Start register (default 0)")
    parser.add_argument("--count",    type=int, default=10,  help="Number of registers to read (default 10)")
    args = parser.parse_args()

    print(f"\n[Modbus TCP Check]  {args.host}:{args.port}  Unit={args.unit}")
    print(f"Reading {args.count} holding registers starting at {args.register} ...\n")

    r = check_modbus(args.host, args.port, args.unit, args.register, args.count)

    if r["connected"]:
        print(f"  TCP connect   : OK")
    else:
        print(f"  TCP connect   : FAILED — {r['error']}")

    if r["read_ok"]:
        print(f"  Modbus read   : OK")
        print(f"  Register[{r['register']}..{r['register']+r['count']-1}] = {r['values']}")
    elif r["connected"]:
        print(f"  Modbus read   : FAILED — {r['error']}")

    out_file = f"modbus_check_{args.host.replace('.','_')}.json"
    with open(out_file, "w") as f:
        json.dump(r, f, indent=2)
    print(f"\n  Result saved → {out_file}")

    sys.exit(0 if r["read_ok"] else 1)


if __name__ == "__main__":
    main()

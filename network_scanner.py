#!/usr/bin/env python3
"""
Industrial Network Scanner - Raspberry Pi Gateway
Digital Twin Phase 1: Device Discovery

Safely scans the local Ethernet subnet to detect active devices.
Methods used: ping (ICMP) + TCP port probing + ARP table lookup
No fuzzing, no brute force, no credentials.

Author: Generated for Schneider Electric Digital Twin project
"""

import socket
import subprocess
import json
import logging
import ipaddress
import threading
import sys
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Optional imports (installed separately if available)
# ---------------------------------------------------------------------------
try:
    import netifaces
    HAS_NETIFACES = True
except ImportError:
    HAS_NETIFACES = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PREFERRED_IFACE   = "eth0"       # First interface to try
PING_TIMEOUT_SEC  = 1            # Seconds to wait for ping reply
TCP_TIMEOUT_SEC   = 0.5          # Seconds to wait for TCP connect
MAX_WORKERS       = 60           # Parallel scan threads
MAX_SUBNET_HOSTS  = 512          # Safety cap: refuse to scan subnets > this
LOG_FILE          = "network_scan.log"
RESULTS_FILE      = "network_scan_results.json"

# Ports to probe and their human-readable service name
PORTS_TO_SCAN: dict[int, str] = {
    80:    "HTTP",
    443:   "HTTPS",
    502:   "Modbus TCP",
    554:   "RTSP",
    4840:  "OPC UA",
    8080:  "HTTP-alt",
    44818: "EtherNet/IP",
}

# Device-type hints derived from open ports (first match wins for display)
PORT_HINTS: dict[int, str] = {
    502:   "Possible Schneider PLC / Modbus device",
    44818: "Possible EtherNet/IP device (Schneider / Rockwell)",
    554:   "Possible camera / RTSP device",
    4840:  "Possible OPC UA server",
    80:    "Possible web interface",
    443:   "Possible web interface (HTTPS)",
    8080:  "Possible web interface (alt port)",
}

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def setup_logging() -> None:
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Network interface detection
# ---------------------------------------------------------------------------
def find_ethernet_interface() -> str:
    """
    Return the name of an active Ethernet interface.
    Priority: eth0 → eth1 → any non-loopback IPv4 interface.
    Falls back to parsing `ip addr show` if netifaces is unavailable.
    """
    candidates = [PREFERRED_IFACE, "eth1", "eth2", "enp3s0", "ens3", "eno1"]

    if HAS_NETIFACES:
        available = netifaces.interfaces()
        for name in candidates:
            if name in available:
                addrs = netifaces.ifaddresses(name)
                if netifaces.AF_INET in addrs:
                    return name
        # Fallback: any non-loopback interface with IPv4
        for name in available:
            if name == "lo":
                continue
            addrs = netifaces.ifaddresses(name)
            if netifaces.AF_INET in addrs:
                return name

    # Fallback: parse `ip -o -4 addr show`
    try:
        out = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1] != "lo":
                return parts[1]
    except Exception:
        pass

    return PREFERRED_IFACE


def get_interface_network(iface: str) -> tuple[str, str, str]:
    """
    Return (local_ip, netmask, subnet_cidr) for the given interface.
    E.g. ('192.168.1.100', '255.255.255.0', '192.168.1.0/24')
    Returns (None, None, None) on failure.
    """
    ip = netmask = subnet = None

    # --- Try netifaces ---
    if HAS_NETIFACES:
        try:
            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                info = addrs[netifaces.AF_INET][0]
                ip      = info.get("addr")
                netmask = info.get("netmask")
        except Exception:
            pass

    # --- Fallback: parse `ip -4 addr show <iface>` ---
    if not ip:
        try:
            out = subprocess.run(
                ["ip", "-4", "addr", "show", iface],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    cidr_str = line.split()[1]          # e.g. 192.168.1.100/24
                    iface_obj = ipaddress.IPv4Interface(cidr_str)
                    ip      = str(iface_obj.ip)
                    netmask = str(iface_obj.netmask)
                    break
        except Exception as exc:
            log.error("Could not parse interface info: %s", exc)

    if ip and netmask:
        try:
            net = ipaddress.IPv4Interface(f"{ip}/{netmask}").network
            subnet = str(net)
        except Exception:
            pass

    return ip, netmask, subnet

# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------
def ping(ip: str) -> bool:
    """Send a single ICMP ping. Returns True if host replies."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(PING_TIMEOUT_SEC), "-n", ip],
            capture_output=True,
            timeout=PING_TIMEOUT_SEC + 1,
        )
        return result.returncode == 0
    except Exception:
        return False

# ---------------------------------------------------------------------------
# TCP port probe
# ---------------------------------------------------------------------------
def tcp_open(ip: str, port: int) -> bool:
    """Return True if TCP port is reachable (SYN-ACK received)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(TCP_TIMEOUT_SEC)
            return s.connect_ex((ip, port)) == 0
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Hostname resolution
# ---------------------------------------------------------------------------
def resolve_hostname(ip: str) -> str | None:
    """Reverse-DNS lookup. Returns hostname string or None."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None

# ---------------------------------------------------------------------------
# MAC address from ARP cache
# ---------------------------------------------------------------------------
def mac_from_arp(ip: str) -> str | None:
    """
    Read MAC address from the kernel ARP table.
    Works without root after a successful ping (which populates the table).
    """
    # Method 1: /proc/net/arp  (Linux, no subprocess)
    try:
        with open("/proc/net/arp") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip:
                    mac = parts[3]
                    if mac not in ("00:00:00:00:00:00", "<incomplete>"):
                        return mac
    except Exception:
        pass

    # Method 2: `arp -n <ip>`
    try:
        out = subprocess.run(
            ["arp", "-n", ip],
            capture_output=True, text=True, timeout=3,
        ).stdout
        for line in out.splitlines():
            if ip in line:
                for token in line.split():
                    if len(token) == 17 and token.count(":") == 5:
                        return token
    except Exception:
        pass

    return None

# ---------------------------------------------------------------------------
# Device-type identification
# ---------------------------------------------------------------------------
def identify_device(open_ports: dict[int, str]) -> list[str]:
    """Return a list of device-type hints based on open ports."""
    hints = []
    seen_hints = set()
    for port in PORT_HINTS:           # respect priority order defined above
        if port in open_ports:
            hint = PORT_HINTS[port]
            if hint not in seen_hints:
                hints.append(hint)
                seen_hints.add(hint)
    return hints

# ---------------------------------------------------------------------------
# Scan a single host
# ---------------------------------------------------------------------------
def scan_host(ip: str) -> dict | None:
    """
    Probe one IP address.
    Returns a result dict if the host is active, None otherwise.
    """
    alive_ping = ping(ip)

    # Probe all ports (even if ping fails – some devices block ICMP)
    open_ports: dict[int, str] = {}
    for port, service in PORTS_TO_SCAN.items():
        if tcp_open(ip, port):
            open_ports[port] = service

    alive = alive_ping or bool(open_ports)
    if not alive:
        return None

    hostname    = resolve_hostname(ip)
    mac         = mac_from_arp(ip) if alive_ping else None
    device_hints = identify_device(open_ports)

    return {
        "ip":           ip,
        "hostname":     hostname,
        "mac":          mac,
        "ping_ok":      alive_ping,
        "open_ports":   {str(p): svc for p, svc in open_ports.items()},
        "device_hints": device_hints,
        "scan_time":    datetime.now().isoformat(),
    }

# ---------------------------------------------------------------------------
# Subnet scan (threaded)
# ---------------------------------------------------------------------------
def scan_subnet(subnet: str, my_ip: str) -> list[dict]:
    """Scan all hosts in the subnet. Skip our own IP."""
    network = ipaddress.IPv4Network(subnet, strict=False)
    all_hosts = list(network.hosts())
    total = len(all_hosts)

    if total > MAX_SUBNET_HOSTS:
        log.error(
            "Subnet %s has %d hosts (limit %d). "
            "Refusing to scan. Use a narrower subnet.",
            subnet, total, MAX_SUBNET_HOSTS,
        )
        sys.exit(1)

    log.info("Scanning %d hosts in %s ...", total, subnet)

    results: list[dict] = []
    completed = 0
    lock = threading.Lock()

    def _scan(ip):
        nonlocal completed
        r = scan_host(str(ip))
        with lock:
            completed += 1
            if completed % 20 == 0 or completed == total:
                pct = int(100 * completed / total)
                print(
                    f"\r  [{pct:3d}%] {completed}/{total} hosts probed ...",
                    end="", flush=True,
                )
        return r

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_scan, ip): ip for ip in all_hosts}
        for fut in as_completed(futures):
            r = fut.result()
            if r and r["ip"] != my_ip:
                results.append(r)

    print()  # newline after progress bar
    return results

# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------
SEP = "─" * 62

def print_device(d: dict) -> None:
    print(f"\n  {SEP}")
    print(f"  IP Address : {d['ip']}")
    if d["hostname"]:
        print(f"  Hostname   : {d['hostname']}")
    if d["mac"]:
        print(f"  MAC Address: {d['mac']}")
    ping_status = "OK" if d["ping_ok"] else "No reply (may block ICMP)"
    print(f"  Ping       : {ping_status}")

    if d["open_ports"]:
        print(f"  Open ports :")
        for port, svc in sorted(d["open_ports"].items(), key=lambda x: int(x[0])):
            print(f"    {int(port):>6}  {svc}")
    else:
        print(f"  Open ports : none detected")

    if d["device_hints"]:
        print(f"  Device type:")
        for hint in d["device_hints"]:
            print(f"    >> {hint}")
    print(f"  {SEP}")


def print_summary(results: list[dict], elapsed: float,
                  my_ip: str, iface: str, subnet: str) -> None:
    print(f"\n\n{'='*62}")
    print(f"  SCAN COMPLETE")
    print(f"  Interface : {iface}   Local IP : {my_ip}")
    print(f"  Subnet    : {subnet}")
    print(f"  Devices   : {len(results)} found in {elapsed:.1f}s")
    print(f"{'='*62}")


def print_no_devices_help() -> None:
    print("""
  No active devices detected. Possible causes:
  ─────────────────────────────────────────────
  1. Ethernet cable not connected or link down
       → Run: ip link show eth0
  2. Wrong interface detected (not eth0)
       → Run: ip -4 addr show
       → Edit PREFERRED_IFACE at top of script
  3. Devices on a different subnet (e.g. 10.x.x.x or 172.x.x.x)
       → Check your switch/router VLAN settings
  4. Firewall on devices blocks all probes
       → Try: ping 192.168.1.1   (manually test known device)
  5. IP not assigned to Raspberry Pi
       → Run: ip -4 addr show eth0
       → Check DHCP or set a static IP
""")

# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------
def save_results(results: list[dict], my_ip: str,
                 iface: str, subnet: str) -> None:
    payload = {
        "scan_metadata": {
            "timestamp":       datetime.now().isoformat(),
            "interface":       iface,
            "raspberry_pi_ip": my_ip,
            "subnet":          subnet,
            "devices_found":   len(results),
            "ports_probed":    PORTS_TO_SCAN,
        },
        "devices": sorted(results, key=lambda d: ipaddress.IPv4Address(d["ip"])),
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    log.info("Results saved → %s", RESULTS_FILE)

# ---------------------------------------------------------------------------
# Quick Modbus connectivity check (standalone helper)
# ---------------------------------------------------------------------------
def modbus_reachability_check(ip: str, port: int = 502) -> None:
    """
    Lightweight check: can we open a TCP connection to port 502?
    Does NOT send any Modbus frame. Use pymodbus for actual reads.
    """
    print(f"\n[Modbus Reachability Check]  {ip}:{port}")
    if tcp_open(ip, port):
        print(f"  ✔  Port 502 is OPEN — Modbus TCP connection possible")
        print(f"  Next step: use pymodbus to read holding registers.")
        print(f"  Example:   python3 modbus_read.py --host {ip}")
    else:
        print(f"  ✘  Port 502 is CLOSED or filtered on {ip}")
        print(f"  Check: firewall rules, PLC Modbus server enabled, correct IP.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    setup_logging()

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║   Industrial Network Scanner — Raspberry Pi Gateway      ║")
    print("║   Digital Twin Phase 1: Device Discovery                 ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    # 1. Detect Ethernet interface
    iface = find_ethernet_interface()
    log.info("Active interface: %s", iface)

    # 2. Get local IP / subnet
    my_ip, netmask, subnet = get_interface_network(iface)
    if not my_ip or not subnet:
        log.error(
            "Cannot determine IP/subnet for interface '%s'. "
            "Is the cable connected? Run: ip -4 addr show %s",
            iface, iface,
        )
        sys.exit(1)

    log.info("Local IP: %s  Netmask: %s  Subnet: %s", my_ip, netmask, subnet)

    # 3. Scan
    t0 = datetime.now()
    results = scan_subnet(subnet, my_ip)
    elapsed = (datetime.now() - t0).total_seconds()

    # 4. Print results
    print_summary(results, elapsed, my_ip, iface, subnet)

    if not results:
        print_no_devices_help()
    else:
        for device in sorted(results, key=lambda d: ipaddress.IPv4Address(d["ip"])):
            print_device(device)
            log.info(
                "FOUND %s | hostname=%s | mac=%s | ports=%s | hints=%s",
                device["ip"], device["hostname"], device["mac"],
                list(device["open_ports"].keys()), device["device_hints"],
            )

    # 5. Save
    save_results(results, my_ip, iface, subnet)

    print(f"\n  Log    → {LOG_FILE}")
    print(f"  JSON   → {RESULTS_FILE}")
    print()

    # 6. If Modbus devices found, offer quick check
    modbus_devices = [d for d in results if "502" in d["open_ports"]]
    if modbus_devices:
        print(f"  {len(modbus_devices)} Modbus device(s) detected. Running reachability check...")
        for d in modbus_devices:
            modbus_reachability_check(d["ip"])


if __name__ == "__main__":
    main()

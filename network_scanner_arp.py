#!/usr/bin/env python3
"""
Industrial Network Scanner — Enhanced ARP version
Raspberry Pi Gateway — Digital Twin Phase 1

Uses Scapy ARP broadcast to discover devices that:
  - Don't reply to ping (ICMP blocked)
  - Don't have open TCP ports on the probed list

Requires root (sudo) because ARP uses raw sockets.
Falls back gracefully to ping+TCP if Scapy is unavailable.

Usage:
    sudo python3 network_scanner_arp.py
"""

import sys
import socket
import subprocess
import json
import logging
import ipaddress
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Re-use helpers from the main scanner
try:
    from network_scanner import (
        setup_logging, find_ethernet_interface, get_interface_network,
        tcp_open, resolve_hostname, identify_device, print_device,
        print_summary, print_no_devices_help, save_results,
        PORTS_TO_SCAN, LOG_FILE, RESULTS_FILE, MAX_WORKERS, MAX_SUBNET_HOSTS,
    )
    IMPORTED_BASE = True
except ImportError:
    IMPORTED_BASE = False

try:
    from scapy.all import ARP, Ether, srp, conf
    HAS_SCAPY = True
except ImportError:
    HAS_SCAPY = False

log = logging.getLogger(__name__)

ARP_TIMEOUT   = 2     # seconds to collect ARP replies
TCP_TIMEOUT   = 0.5

# ---------------------------------------------------------------------------
# ARP sweep — discovers all devices that respond to ARP broadcast
# ---------------------------------------------------------------------------
def arp_sweep(subnet: str, iface: str) -> dict[str, str]:
    """
    Broadcast an ARP "who-has" for every IP in subnet.
    Returns {ip: mac} for all responders.
    Requires Scapy + root privileges.
    """
    if not HAS_SCAPY:
        log.warning("Scapy not installed — skipping ARP sweep.")
        return {}

    if os.geteuid() != 0:
        log.warning("ARP sweep needs root. Run with: sudo python3 %s", sys.argv[0])
        return {}

    log.info("Running ARP broadcast sweep on %s via %s ...", subnet, iface)

    conf.verb = 0   # suppress Scapy output
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=subnet)
    answered, _ = srp(pkt, iface=iface, timeout=ARP_TIMEOUT, multi=False)

    results: dict[str, str] = {}
    for _, rcv in answered:
        ip  = rcv[ARP].psrc
        mac = rcv[Ether].src
        results[ip] = mac

    log.info("ARP sweep: %d host(s) replied.", len(results))
    return results


# ---------------------------------------------------------------------------
# Enhanced scan: ARP first, then TCP probe
# ---------------------------------------------------------------------------
def enhanced_scan(subnet: str, my_ip: str, iface: str) -> list[dict]:
    import os

    # Step 1: ARP broadcast (gets everyone, even ICMP-blocked devices)
    arp_map = arp_sweep(subnet, iface)   # {ip: mac}

    # Step 2: Build candidate list = ARP responders ∪ full subnet (for TCP)
    network   = ipaddress.IPv4Network(subnet, strict=False)
    all_hosts = [str(h) for h in network.hosts() if str(h) != my_ip]

    if len(all_hosts) > MAX_SUBNET_HOSTS:
        log.error("Subnet too large (%d hosts). Limit: %d.", len(all_hosts), MAX_SUBNET_HOSTS)
        sys.exit(1)

    log.info("TCP port probing %d hosts ...", len(all_hosts))

    results: list[dict] = []
    completed = 0
    lock = threading.Lock()

    def probe(ip: str) -> dict | None:
        nonlocal completed

        # Was this IP discovered by ARP?
        mac       = arp_map.get(ip)
        arp_alive = ip in arp_map

        # TCP port scan
        open_ports: dict[int, str] = {}
        for port, service in PORTS_TO_SCAN.items():
            if tcp_open(ip, port):
                open_ports[port] = service

        alive = arp_alive or bool(open_ports)

        with lock:
            completed += 1
            if completed % 20 == 0 or completed == len(all_hosts):
                pct = int(100 * completed / len(all_hosts))
                print(
                    f"\r  [{pct:3d}%] {completed}/{len(all_hosts)} hosts probed ...",
                    end="", flush=True,
                )

        if not alive:
            return None

        return {
            "ip":           ip,
            "hostname":     resolve_hostname(ip),
            "mac":          mac,
            "ping_ok":      arp_alive,      # ARP ≈ "alive" for this version
            "arp_detected": arp_alive,
            "open_ports":   {str(p): svc for p, svc in open_ports.items()},
            "device_hints": identify_device(open_ports),
            "scan_time":    datetime.now().isoformat(),
        }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(probe, ip): ip for ip in all_hosts}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    print()
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    import os

    if not IMPORTED_BASE:
        print("ERROR: network_scanner.py not found. Place both files in the same directory.")
        sys.exit(1)

    setup_logging()

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║   Industrial Network Scanner (ARP enhanced)              ║")
    print("║   Raspberry Pi Gateway — Digital Twin Phase 1            ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    if not HAS_SCAPY:
        print("  [WARNING] Scapy not installed. ARP sweep disabled.")
        print("  Install: sudo pip3 install scapy")
        print("  Falling back to ping+TCP mode only.\n")

    if os.geteuid() != 0:
        print("  [WARNING] Not running as root. ARP sweep requires sudo.")
        print("  Run: sudo python3 network_scanner_arp.py\n")

    iface = find_ethernet_interface()
    log.info("Active interface: %s", iface)

    my_ip, netmask, subnet = get_interface_network(iface)
    if not my_ip or not subnet:
        log.error("Cannot determine IP/subnet. Check Ethernet connection.")
        sys.exit(1)

    log.info("Local IP: %s  Netmask: %s  Subnet: %s", my_ip, netmask, subnet)

    t0 = datetime.now()
    results = enhanced_scan(subnet, my_ip, iface)
    elapsed = (datetime.now() - t0).total_seconds()

    print_summary(results, elapsed, my_ip, iface, subnet)

    if not results:
        print_no_devices_help()
    else:
        for device in sorted(results, key=lambda d: ipaddress.IPv4Address(d["ip"])):
            print_device(device)
            log.info(
                "FOUND %s | mac=%s | arp=%s | ports=%s | hints=%s",
                device["ip"], device["mac"], device.get("arp_detected"),
                list(device["open_ports"].keys()), device["device_hints"],
            )

    save_results(results, my_ip, iface, subnet)

    print(f"\n  Log    → {LOG_FILE}")
    print(f"  JSON   → {RESULTS_FILE}")
    print()


if __name__ == "__main__":
    import os
    main()

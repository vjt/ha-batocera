"""Wake-on-LAN: the magic packet Home Assistant sends to power a box on.

Sent by Home Assistant itself, to the directed broadcast address of the
box's subnet: Home Assistant may have several interfaces, and the limited
broadcast (255.255.255.255) leaves from the default route's, which need not
be the box's.
"""

from __future__ import annotations

import ipaddress
import re
import socket

PORT = 9  # discard: what Wake-on-LAN senders use
LIMITED_BROADCAST = "255.255.255.255"  # when the box's subnet is not known
_MAC = re.compile(r"^[0-9a-f]{2}([:-]?)[0-9a-f]{2}(\1[0-9a-f]{2}){4}$")


def mac_address(text: object) -> str | None:
    """A MAC address as aa:bb:cc:dd:ee:ff, from that, with dashes or bare;
    None when it is not one."""
    if not isinstance(text, str) or not _MAC.match(mac := text.strip().lower()):
        return None
    digits = re.sub(r"[:-]", "", mac)
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def ipv4_address(text: object) -> str | None:
    """An IPv4 address as text, None when it is not one."""
    if not isinstance(text, str):
        return None
    try:
        return str(ipaddress.IPv4Address(text.strip()))
    except ValueError:
        return None


def magic_packet(mac: str) -> bytes:
    """Six 0xFF, then the MAC address sixteen times."""
    return b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16


def send_magic_packet(mac: str, broadcast: str) -> None:
    """Send it over UDP. Blocking: run it in the executor."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic_packet(mac), (broadcast, PORT))

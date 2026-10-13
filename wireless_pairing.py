"""Extract Android wireless pairing details without storing the one-time code."""

from __future__ import annotations

import ipaddress
import re
import xml.etree.ElementTree as ET


_ENDPOINT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3}):([1-9]\d{0,4})(?!\d)")
_CODE = re.compile(r"\d{6}")
_CODE_IDS = {"pairing_code", "pairing_code_value", "adb_pairing_code", "six_digit_code"}
_ADDRESS_IDS = {"ip_addr", "pairing_ip_address", "pairing_address"}


def valid_endpoint(text: str) -> str:
    """Return a validated IPv4:port found in text, or an empty string."""
    for match in _ENDPOINT.finditer(text):
        try:
            host = ipaddress.IPv4Address(match.group(1))
        except ipaddress.AddressValueError:
            continue
        port = int(match.group(2))
        if not (host.is_loopback or host.is_unspecified or host.is_multicast) and port <= 65535:
            return f"{host}:{port}"
    return ""


def parse_pairing_dialog(output: str) -> tuple[str, str]:
    """Read code and endpoint only from a visible Android Settings pairing dialog."""
    start = output.find("<hierarchy")
    end = output.find("</hierarchy>", start)
    if start < 0 or end < 0:
        return "", ""
    try:
        root = ET.fromstring(output[start:end + len("</hierarchy>")])
    except ET.ParseError:
        return "", ""

    nodes = [
        node for node in root.iter("node")
        if "settings" in node.get("package", "").casefold()
    ]
    if not nodes:
        return "", ""

    def values(node: ET.Element) -> tuple[str, str]:
        return node.get("text", "").strip(), node.get("content-desc", "").strip()

    code = ""
    for node in nodes:
        resource_id = node.get("resource-id", "").rsplit("/", 1)[-1]
        if resource_id in _CODE_IDS:
            code = next((value for value in values(node) if _CODE.fullmatch(value)), "")
            if code:
                break

    if not code:
        visible_text = " ".join(value for node in nodes for value in values(node))
        normalized = visible_text.casefold()
        pairing_context = "配对码" in visible_text or "pairing code" in normalized
        candidates = {value for node in nodes for value in values(node) if _CODE.fullmatch(value)}
        if pairing_context and len(candidates) == 1:
            code = candidates.pop()
    if not code:
        return "", ""

    endpoints = []
    for node in nodes:
        resource_id = node.get("resource-id", "").rsplit("/", 1)[-1]
        if resource_id in _ADDRESS_IDS:
            endpoints.extend(endpoint for value in values(node) if (endpoint := valid_endpoint(value)))
    if not endpoints:
        endpoints = list({
            endpoint for node in nodes for value in values(node)
            if (endpoint := valid_endpoint(value))
        })
    return code, endpoints[0] if len(set(endpoints)) == 1 else ""


def pairing_service_endpoints(output: str) -> list[str]:
    """List only code-pairing mDNS services; studio-* services are QR pairing."""
    endpoints = []
    for line in output.splitlines():
        parts = line.split()
        if (len(parts) >= 3 and parts[0].startswith("adb-")
                and parts[1] == "_adb-tls-pairing._tcp"):
            endpoint = valid_endpoint(parts[2])
            if endpoint and endpoint not in endpoints:
                endpoints.append(endpoint)
    return endpoints


def choose_pairing_endpoint(endpoints: list[str], phone_ip: str = "") -> str:
    """Avoid selecting a different phone when several pairing servers exist."""
    if phone_ip:
        matches = [endpoint for endpoint in endpoints if endpoint.partition(":")[0] == phone_ip]
        return matches[0] if len(matches) == 1 else ""
    return endpoints[0] if len(endpoints) == 1 else ""

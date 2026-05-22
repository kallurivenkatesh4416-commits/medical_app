"""Medical-record malware scan gateway.

Uploads use a deterministic stub in local/test and a small clamd INSTREAM
client when production config points at ClamAV. The record service scans before
any storage write so a failed or unavailable scan never creates an object.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Protocol

from app.config import get_settings

_EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
    b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


class VirusScanUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    scanner: str
    signature: str | None = None


class VirusScanGateway(Protocol):
    def scan(self, *, data: bytes, file_name: str) -> ScanResult: ...


class StubVirusScanGateway:
    def scan(self, *, data: bytes, file_name: str) -> ScanResult:
        if _EICAR in data:
            return ScanResult(
                clean=False,
                scanner="stub",
                signature="Eicar-Test-Signature",
            )
        return ScanResult(clean=True, scanner="stub")


class ClamAVVirusScanGateway:
    """Small clamd INSTREAM client. Config is validated at scan time."""

    def __init__(self, *, host: str | None, port: int) -> None:
        self._host = host
        self._port = port

    def scan(self, *, data: bytes, file_name: str) -> ScanResult:
        if not self._host:
            raise VirusScanUnavailable("CLAMAV_HOST is required when VIRUS_SCAN_MODE=clamav")
        try:
            with socket.create_connection((self._host, self._port), timeout=10) as sock:
                sock.settimeout(10)
                sock.sendall(b"zINSTREAM\0")
                for offset in range(0, len(data), 64 * 1024):
                    chunk = data[offset : offset + 64 * 1024]
                    sock.sendall(struct.pack(">I", len(chunk)))
                    sock.sendall(chunk)
                sock.sendall(struct.pack(">I", 0))
                reply_bytes = bytearray()
                while chunk := sock.recv(4096):
                    reply_bytes.extend(chunk)
                reply = bytes(reply_bytes).decode("utf-8", errors="replace").strip("\0\n ")
        except OSError as exc:
            raise VirusScanUnavailable("ClamAV scan is unavailable") from exc

        if reply.endswith(" OK"):
            return ScanResult(clean=True, scanner="clamav")
        if reply.endswith(" FOUND"):
            signature = reply.split(":", 1)[-1].rsplit(" FOUND", 1)[0].strip()
            return ScanResult(
                clean=False,
                scanner="clamav",
                signature=signature[:160] or "ClamAV-Signature",
            )
        raise VirusScanUnavailable("ClamAV returned an unexpected scan response")


def get_virus_scan_gateway() -> VirusScanGateway:
    settings = get_settings()
    mode = settings.virus_scan_mode.lower()
    if mode == "stub":
        return StubVirusScanGateway()
    if mode == "clamav":
        return ClamAVVirusScanGateway(
            host=settings.clamav_host,
            port=settings.clamav_port,
        )
    raise VirusScanUnavailable("VIRUS_SCAN_MODE must be 'stub' or 'clamav'")

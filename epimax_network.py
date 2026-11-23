"""Read-only experimental TCP transports for EpiMax PVCi-compatible endpoints.

The documented PVCi/PVCiDuo/PVCuni/PVCduo RJ45 sockets are RS232/RS485,
NOT Ethernet. This transport works only for an independently confirmed
Modbus-TCP device, or a correctly wired serial-to-TCP gateway.
"""
from __future__ import annotations
import socket
import struct
from urllib.parse import urlsplit
from vacuum_extra_devices import EpimaxPVCDevice, epimax_fc23_read_request, parse_epimax_fc23_response

def make_endpoint(host: str, port: str | int, mode: str) -> str:
    scheme = "tcp" if mode == "modbus_tcp" else "rtu+tcp" if mode == "rtu_tcp_bridge" else None
    if scheme is None:
        raise ValueError("Select Modbus TCP or RTU/TCP gateway")
    host = str(host).strip()
    if not host or any(c in host for c in "/?#@ \\\t\r\n"):
        raise ValueError("Enter a valid EpiMax IP address or hostname")
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        raise ValueError("For IPv6, surround the address with [brackets]")
    try:
        number = int(port)
    except (ValueError, TypeError) as exc:
        raise ValueError("Enter the controller TCP port (1–65535)") from exc
    if not 1 <= number <= 65535:
        raise ValueError("TCP port must be 1–65535")
    endpoint = f"{scheme}://{host}:{number}"
    parsed = urlsplit(endpoint)
    if not parsed.hostname or parsed.port != number or parsed.username is not None:
        raise ValueError("Invalid network endpoint")
    return endpoint

def parse_endpoint(endpoint: str) -> tuple[str, int, str]:
    parsed = urlsplit(endpoint)
    modes = {"tcp":"modbus_tcp", "rtu+tcp":"rtu_tcp_bridge"}
    if parsed.scheme not in modes or parsed.username is not None or parsed.password is not None:
        raise ValueError("Expected tcp://HOST:PORT or rtu+tcp://HOST:PORT")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Network endpoint must contain only host and port")
    port = parsed.port
    if not parsed.hostname or port is None or not 1 <= port <= 65535:
        raise ValueError("Network endpoint needs a host and TCP port")
    return parsed.hostname, port, modes[parsed.scheme]

def _exact(sock: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        piece = sock.recv(count-len(chunks))
        if not piece:
            raise ConnectionError("EpiMax TCP connection closed while reading")
        chunks.extend(piece)
    return bytes(chunks)

def parse_modbus_tcp_reply(pdu: bytes, count: int) -> bytes:
    if len(pdu) >= 2 and pdu[0] == 0x97:
        raise ValueError(f"EpiMax Modbus TCP exception {pdu[1]}")
    if len(pdu) != 2 + count * 2 or pdu[0] != 0x17 or pdu[1] != count * 2:
        raise ValueError("Unexpected EpiMax Modbus TCP reply")
    return pdu[2:]

class EpimaxTCPDevice(EpimaxPVCDevice):
    """TCP transport only; pressure registers remain PVCi-specific."""
    def __init__(self, endpoint: str, *, address: int=1, pressure_units: str="mBar"):
        host, tcp_port, transport = parse_endpoint(endpoint)
        super().__init__(endpoint, address=address, pressure_units=pressure_units)
        self.host, self.tcp_port, self.transport = host, tcp_port, transport
        self.sock: socket.socket | None = None
        self._transaction_id = 0
    def open(self):
        with self.lock:
            if self.sock is not None:
                return
            try:
                self.sock = socket.create_connection((self.host,self.tcp_port),timeout=1.5)
                self.sock.settimeout(1.5)
            except OSError as exc:
                self.sock = None
                raise ConnectionError(
                    f"Cannot connect to {self.host}:{self.tcp_port} (TCP). "
                    "Check Ethernet capability, link status, IP and firewall. "
                    "An RJ45 serial socket cannot work through a USB Ethernet NIC."
                ) from exc
    def close(self):
        with self.lock:
            if self.sock is not None:
                self.sock.close()
                self.sock = None
    def read_registers(self, start: int, count: int=2) -> bytes:
        with self.lock:
            if self.sock is None:
                raise ConnectionError("EpiMax TCP connection is closed")
            request=epimax_fc23_read_request(self.address,start,count)
            try:
                if self.transport == "rtu_tcp_bridge":
                    self.sock.sendall(request)
                    head=_exact(self.sock,3)
                    if head[1] & 0x80:
                        tail=_exact(self.sock,2)
                    elif head[2] <= 64:
                        tail=_exact(self.sock,head[2]+2)
                    else:
                        raise ValueError("Unreasonable Modbus RTU response length")
                    return parse_epimax_fc23_response(head+tail,self.address,count)
                self._transaction_id=(self._transaction_id+1)&0xFFFF
                tid=self._transaction_id
                pdu=request[1:-2]
                self.sock.sendall(struct.pack(">HHHB",tid,0,1+len(pdu),self.address)+pdu)
                header=_exact(self.sock,7)
                response_tid,protocol,length,unit=struct.unpack(">HHHB",header)
                if response_tid!=tid or protocol!=0 or unit!=self.address or not 2 <= length <= 68:
                    raise ValueError("Invalid EpiMax Modbus TCP transaction header")
                return parse_modbus_tcp_reply(_exact(self.sock,length-1),count)
            except (ConnectionError,OSError,TimeoutError) as exc:
                self.close()
                raise ConnectionError("EpiMax TCP communication failed; reconnect required") from exc
    def read_status(self):
        status=super().read_status()
        status.raw=("EpiMax Modbus TCP (experimental)" if self.transport=="modbus_tcp"
                    else "EpiMax Modbus RTU through TCP gateway")
        return status

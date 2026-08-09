from __future__ import annotations

import base64
import json
import socket
import struct
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import tyro
from pydantic import BaseModel

from .. import machine_role

X18_OSC_PORT = 10_024
XREMOTE_INTERVAL_SECONDS = 8.0
SOCKET_TIMEOUT_SECONDS = 0.2


class X18RecorderOptions(BaseModel, frozen=True):
    host: str
    port: int = X18_OSC_PORT
    log_dir: Path = Path(".")


def main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine("showco run x18-record")
    options = tyro.cli(
        X18RecorderOptions,
        args=argv,
        description="Record X18 OSC traffic",
    )
    return record_osc(options)


def record_osc(options: X18RecorderOptions) -> int:
    recorder = X18OscRecorder(options.host, port=options.port, log_dir=options.log_dir)
    print(f"recording X18 OSC to {recorder.path}")
    recorder.run_forever()
    return 0


def xremote_message() -> bytes:
    return osc_string("/xremote") + osc_string(",")


def osc_string(value: str) -> bytes:
    data = value.encode() + b"\0"
    return data + b"\0" * padding(len(data))


def padding(length: int) -> int:
    return (4 - length % 4) % 4


def log_path(log_dir: Path, timestamp: datetime | None = None) -> Path:
    timestamp = timestamp or datetime.now(UTC)
    return log_dir / f"x18-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.jsonl"


def decode_osc(data: bytes) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    try:
        parse_packet(data, messages)
    except ValueError as e:
        return [{"error": str(e)}]
    return messages


def parse_packet(data: bytes, messages: list[dict[str, object]]) -> None:
    if data.startswith(b"#bundle\0"):
        parse_bundle(data, messages)
    else:
        messages.append(parse_message(data))


def parse_bundle(data: bytes, messages: list[dict[str, object]]) -> None:
    offset = 16
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("truncated OSC bundle element size")
        size = int.from_bytes(data[offset : offset + 4], "big")
        offset += 4
        if offset + size > len(data):
            raise ValueError("truncated OSC bundle element")
        parse_packet(data[offset : offset + size], messages)
        offset += size


def parse_message(data: bytes) -> dict[str, object]:
    path, offset = read_string(data, 0)
    types, offset = read_string(data, offset)
    if not types.startswith(","):
        return {"path": path, "types": "", "args": []}
    args: list[object] = []
    for t in types[1:]:
        value, offset = read_arg(t, data, offset)
        args.append(value)
    return {"path": path, "types": types[1:], "args": args}


def read_string(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\0", offset)
    if end < 0:
        raise ValueError("unterminated OSC string")
    value = data[offset:end].decode(errors="replace")
    next_offset = end + 1 + padding(end + 1 - offset)
    if next_offset > len(data):
        raise ValueError("truncated OSC string padding")
    return value, next_offset


def read_arg(arg_type: str, data: bytes, offset: int) -> tuple[object, int]:
    if arg_type == "i":
        if offset + 4 > len(data):
            raise ValueError("truncated OSC int")
        return int.from_bytes(data[offset : offset + 4], "big", signed=True), offset + 4
    if arg_type == "f":
        if offset + 4 > len(data):
            raise ValueError("truncated OSC float")
        return struct.unpack(">f", data[offset : offset + 4])[0], offset + 4
    if arg_type == "s":
        return read_string(data, offset)
    if arg_type == "b":
        return read_blob(data, offset)
    if arg_type == "T":
        return True, offset
    if arg_type == "F":
        return False, offset
    if arg_type == "N":
        return None, offset
    raise ValueError(f"unsupported OSC argument type {arg_type}")


def read_blob(data: bytes, offset: int) -> tuple[str, int]:
    if offset + 4 > len(data):
        raise ValueError("truncated OSC blob size")
    size = int.from_bytes(data[offset : offset + 4], "big")
    offset += 4
    if offset + size > len(data):
        raise ValueError("truncated OSC blob")
    value = base64.b64encode(data[offset : offset + size]).decode("ascii")
    return value, offset + size + padding(size)


class X18OscRecorder:
    def __init__(
        self,
        host: str,
        *,
        port: int = X18_OSC_PORT,
        log_dir: Path = Path("."),
        subscribe_interval: float = XREMOTE_INTERVAL_SECONDS,
        socket_timeout: float = SOCKET_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host
        self.port = port
        self.log_dir = log_dir
        self.subscribe_interval = subscribe_interval
        self.socket_timeout = socket_timeout
        self.path = log_path(log_dir)

    def run_forever(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as output:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self.socket_timeout)
                next_subscribe = 0.0
                while True:
                    now = time.monotonic()
                    if now >= next_subscribe:
                        self.send_xremote(sock, output)
                        next_subscribe = now + self.subscribe_interval
                    try:
                        data, source = sock.recvfrom(65_535)
                    except TimeoutError:
                        continue
                    self.write_datagram(output, "in", data, source=source)

    def send_xremote(self, sock: socket.socket, output: BinaryIO) -> None:
        data = xremote_message()
        target = (self.host, self.port)
        try:
            sock.sendto(data, target)
        except OSError as e:
            self.write_error(output, "out", target, str(e))
            return
        self.write_datagram(output, "out", data, target=target)

    def write_error(
        self,
        output: BinaryIO,
        direction: str,
        target: tuple[str, int],
        error: str,
    ) -> None:
        record: dict[str, object] = {
            "time": time.time(),
            "monotonic": time.monotonic(),
            "direction": direction,
            "kind": "error",
            "target": [target[0], target[1]],
            "error": error,
        }
        output.write(json.dumps(record, separators=(",", ":")).encode() + b"\n")
        output.flush()

    def write_datagram(
        self,
        output: BinaryIO,
        direction: str,
        data: bytes,
        *,
        source: tuple[str, int] | None = None,
        target: tuple[str, int] | None = None,
    ) -> None:
        record: dict[str, object] = {
            "time": time.time(),
            "monotonic": time.monotonic(),
            "direction": direction,
            "kind": "osc",
            "data_b64": base64.b64encode(data).decode("ascii"),
            "decoded": decode_osc(data),
        }
        if source:
            record["source"] = [source[0], source[1]]
        if target:
            record["target"] = [target[0], target[1]]
        output.write(json.dumps(record, separators=(",", ":")).encode() + b"\n")
        output.flush()

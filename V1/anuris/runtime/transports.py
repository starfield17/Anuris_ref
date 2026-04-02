from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Dict


WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def encode_sse_event(event: Dict[str, Any]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False)
    event_type = str(event.get("type") or "message")
    return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")


def websocket_accept_value(key: str) -> str:
    raw = (str(key or "") + WEBSOCKET_GUID).encode("utf-8")
    return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")


def encode_websocket_text(text: str) -> bytes:
    data = text.encode("utf-8")
    length = len(data)
    if length < 126:
        header = bytes([0x81, length])
    elif length < 65536:
        header = bytes([0x81, 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([0x81, 127]) + length.to_bytes(8, "big")
    return header + data


def decode_websocket_frame(frame: bytes) -> str:
    if len(frame) < 6:
        return ""
    masked = bool(frame[1] & 0x80)
    if not masked:
        return ""
    length = frame[1] & 0x7F
    index = 2
    if length == 126:
        length = int.from_bytes(frame[index:index + 2], "big")
        index += 2
    elif length == 127:
        length = int.from_bytes(frame[index:index + 8], "big")
        index += 8
    mask = frame[index:index + 4]
    index += 4
    data = frame[index:index + length]
    decoded = bytes(value ^ mask[pos % 4] for pos, value in enumerate(data))
    return decoded.decode("utf-8", errors="ignore")

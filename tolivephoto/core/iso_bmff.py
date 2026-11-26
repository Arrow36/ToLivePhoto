"""ISO-BMFF / QuickTime atom parser and chunk offset patcher."""
from __future__ import annotations

import struct
from dataclasses import dataclass

CONTAINER_TYPES = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"dinf",
    b"stbl",
    b"edts",
    b"tref",
    b"gmhd",
    b"udta",
    b"ilst",
}


@dataclass(frozen=True, slots=True)
class Atom:
    start: int
    size: int
    type: bytes
    header_size: int = 8

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def payload_start(self) -> int:
        return self.start + self.header_size

    def raw(self, data: bytes | bytearray) -> bytes:
        return bytes(data[self.start : self.end])


def atom(kind: bytes, payload: bytes) -> bytes:
    if len(kind) != 4:
        raise ValueError("ISO-BMFF atom type must be 4 bytes")
    size = len(payload) + 8
    if size <= 0xFFFFFFFF:
        return struct.pack(">I4s", size, kind) + payload
    return struct.pack(">I4sQ", 1, kind, len(payload) + 16) + payload


def parse_atoms(data: bytes | bytearray, start: int = 0, end: int | None = None) -> list[Atom]:
    boundary = len(data) if end is None else end
    if start < 0 or boundary > len(data) or start > boundary:
        raise ValueError("Invalid ISO-BMFF parse range")
    result: list[Atom] = []
    position = start
    while position < boundary:
        if position + 8 > boundary:
            raise ValueError("Truncated ISO-BMFF atom header")
        size = int.from_bytes(data[position : position + 4], "big")
        kind = bytes(data[position + 4 : position + 8])
        header = 8
        if size == 1:
            if position + 16 > boundary:
                raise ValueError("Truncated 64-bit ISO-BMFF atom header")
            size = int.from_bytes(data[position + 8 : position + 16], "big")
            header = 16
        elif size == 0:
            size = boundary - position
        if size < header or position + size > boundary:
            label = kind.decode("latin-1", errors="replace")
            raise ValueError(f"ISO-BMFF atom {label} exceeds boundary")
        result.append(Atom(position, size, kind, header))
        position += size
    return result


def child_atoms(data: bytes | bytearray, parent: Atom) -> list[Atom]:
    return parse_atoms(data, parent.payload_start, parent.end)


def first_child(data: bytes | bytearray, parent: Atom, kind: bytes) -> Atom | None:
    return next((item for item in child_atoms(data, parent) if item.type == kind), None)


def patch_chunk_offsets(moov: bytes, delta: int) -> bytes:
    """Shift every stco/co64 entry in a complete moov atom by delta."""
    payload = bytearray(moov)
    top = parse_atoms(payload)
    if len(top) != 1 or top[0].type != b"moov":
        raise ValueError("Expected a complete moov atom")

    def visit(parent: Atom) -> None:
        for item in child_atoms(payload, parent):
            if item.type in {b"stco", b"co64"}:
                count_at = item.payload_start + 4
                count = int.from_bytes(payload[count_at : count_at + 4], "big")
                width = 4 if item.type == b"stco" else 8
                entries = count_at + 4
                for index in range(count):
                    position = entries + index * width
                    old = int.from_bytes(payload[position : position + width], "big")
                    new = old + delta
                    if new < 0 or new >= 1 << (width * 8):
                        raise ValueError("Chunk offset out of range after patching")
                    payload[position : position + width] = new.to_bytes(width, "big")
            elif item.type in CONTAINER_TYPES:
                visit(item)

    visit(top[0])
    return bytes(payload)

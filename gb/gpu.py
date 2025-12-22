from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List

VRAM_BEGIN = 0x8000
VRAM_END   = 0x9FFF
VRAM_SIZE  = VRAM_END - VRAM_BEGIN + 1

TILE_DATA_SIZE = 0x1800
NUM_TILES = TILE_DATA_SIZE // 16


class TilePixelValue(IntEnum):
    Zero  = 0
    One   = 1
    Two   = 2
    Three = 3


Tile = List[List[TilePixelValue]]


def empty_tile() -> Tile:
    return [[TilePixelValue.Zero for _ in range(8)] for _ in range(8)]


@dataclass
class GPU:
    vram: bytearray = field(default_factory=lambda: bytearray(VRAM_SIZE))
    tile_set: List[Tile] = field(default_factory=lambda: [empty_tile() for _ in range(NUM_TILES)])

    def read_vram(self, index: int) -> int:
        return self.vram[index] & 0xFF

    def write_vram(self, index: int, value: int) -> None:
        value &= 0xFF
        self.vram[index] = value

        if index >= TILE_DATA_SIZE:
            return

        normalized = index & 0xFFFE
        byte1 = self.vram[normalized]
        byte2 = self.vram[normalized + 1]

        tile_index = index // 16
        row_index  = (index % 16) // 2

        for x in range(8):
            mask = 1 << (7 - x)
            lsb = 1 if (byte1 & mask) else 0
            msb = 1 if (byte2 & mask) else 0
            val = (msb << 1) | lsb
            self.tile_set[tile_index][row_index][x] = TilePixelValue(val)

# src/dst2ak/blockreader.py

from __future__ import annotations
import gzip
import io
import os
import struct
from typing import Iterator, Tuple

BLOCK_LEN = 32000  # matches C BlockLen

# ---------- CRC-CCITT (translated from dst_crc_ccitt.c) ----------

# nibble-reversal lookup used to build rchr[]
_IT = [
    0x0, 0x8, 0x4, 0xC, 0x2, 0xA, 0x6, 0xE,
    0x1, 0x9, 0x5, 0xD, 0x3, 0xB, 0x7, 0xF,
]

def _build_rchr() -> list[int]:
    r = [0] * 256
    for j in range(256):
        r[j] = (_IT[j & 0x0F] << 4) | _IT[(j >> 4) & 0x0F]
    return r

_RCHR = _build_rchr()

def _dst_icrc1(crc: int, onech: int) -> int:
    """Core step from dst_icrc1: polynomial 0x1021, 16-bit arithmetic."""
    ans = (crc ^ ((onech & 0xFF) << 8)) & 0xFFFF
    for _ in range(8):
        if ans & 0x8000:
            ans = ((ans << 1) ^ 0x1021) & 0xFFFF
        else:
            ans = (ans << 1) & 0xFFFF
    return ans

# Precompute icrctab[j] = dst_icrc1(j<<8, 0)
_ICRCTAB = [_dst_icrc1(j << 8, 0) for j in range(256)]

def _crc_ccitt_dst(payload: bytes) -> int:
    """
    Reproduces dst_crc_ccitt_:
      - reflect each input byte via RCHR (jrev < 0)
      - table update: c = icrctab[b ^ HIBYTE(c)] ^ (LOBYTE(c) << 8)
      - final reflect-out of the 16-bit cword
    Returns 16-bit CRC as an int.
    """
    cword = 0  # jinit = 0 in the C code
    for b in payload:
        idx = _RCHR[b] ^ ((cword >> 8) & 0xFF)
        cword = (_ICRCTAB[idx] ^ ((cword & 0xFF) << 8)) & 0xFFFF

    # final reflect-out (since jrev < 0)
    hi = (cword >> 8) & 0xFF
    lo = cword & 0xFF
    return (_RCHR[hi] | (_RCHR[lo] << 8)) & 0xFFFF

# ---------- BlockReader ----------

class BlockReader:
    """
    Iterate over 32,000-byte blocks.
    Yields (block_index, payload_without_crc) after verifying the CRC.
    """
    def __init__(self, path: str):
        self.path = path
        self._fh: io.BufferedReader | gzip.GzipFile | None = None
        self._idx = 0

    def __enter__(self) -> "BlockReader":
        if self.path.endswith(".gz"):
            self._fh = gzip.open(self.path, "rb")
        else:
            self._fh = open(self.path, "rb")
        self._idx = 0
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fh:
            self._fh.close()
        self._fh = None

    def __iter__(self) -> Iterator[Tuple[int, bytes]]:
        if self._fh is None:
            raise RuntimeError("BlockReader must be used as a context manager")
        while True:
            block = self._fh.read(BLOCK_LEN)
            if not block:
                break
            if len(block) != BLOCK_LEN:
                raise ValueError(f"Short block: expected {BLOCK_LEN} bytes, got {len(block)}")

            payload = block[:-4]
            # CRC is packed via dst_packi4_ => 4 bytes, little-endian; only low 16 bits carry value.
            (crc_le32,) = struct.unpack("<I", block[-4:])
            crc_expected = crc_le32 & 0xFFFF

            crc_actual = _crc_ccitt_dst(payload)
            if crc_actual != crc_expected:
                raise ValueError(
                    f"CRC mismatch at block {self._idx}: expected {crc_expected:#06x}, got {crc_actual:#06x}")

            yield (self._idx, payload)
            self._idx += 1


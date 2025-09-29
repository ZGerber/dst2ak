from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator, Optional
import struct

from .blockreader import _crc_ccitt_dst as crc_ccitt  # same CRC the C uses

# Stream-level opcodes (single-byte verbs that follow 0x60 = OPCODE)
OPCODE             = 0x60
START_BLOCK        = 97
END_BLOCK_LOGICAL  = 98
END_BLOCK_PHYSICAL = 99
FILLER             = 100

# Bank verbs
START_BANK  = 7
CONTINUE    = 8
END_BANK    = 14
TO_BE_CONTD = 15  # followed by 5 trailing bytes to skip

@dataclass
class Bank:
    bank_id: int
    bank_version: int
    data: bytes     # full bank payload as written between START/CONT segments

class _ByteStream:
    """
    Simple byte-stream over the concatenated block payloads.
    Mirrors dst_read_bank_: advances one byte at a time, reading <I where needed.
    """
    def __init__(self, blocks: Iterator[tuple[int, bytes]]):
        self._blocks = iter(blocks)
        self._buf = b""
        self._i = 0
        self._eof = False

    def _fill(self) -> None:
        if self._i < len(self._buf):  # still data in current block
            return
        if self._eof:
            return
        try:
            _, self._buf = next(self._blocks)
            self._i = 0
        except StopIteration:
            self._buf = b""
            self._i = 0
            self._eof = True

    def read1(self) -> Optional[int]:
        self._fill()
        if self._eof:
            return None
        b = self._buf[self._i]
        self._i += 1
        return b

    def read_exact(self, n: int) -> Optional[bytes]:
        out = bytearray()
        while len(out) < n:
            self._fill()
            if self._eof:
                return None
            # copy as much as possible from current buffer
            take = min(n - len(out), len(self._buf) - self._i)
            out += self._buf[self._i:self._i+take]
            self._i += take
        return bytes(out)

    def read_u32le(self) -> Optional[int]:
        b = self.read_exact(4)
        if b is None:
            return None
        return struct.unpack("<I", b)[0]


class BankAssembler:
    """
    Consumes block payloads and yields complete Bank objects.

    Behavior mirrors dst_read_bank_:
      - scan for OPCODE (0x60); skip stray bytes until found
      - handle block-level verbs (START_BLOCK, END_BLOCK_*, FILLER)
      - START_BANK / CONTINUE: next <i4 = segment length in BYTES; copy that many bytes into current bank
      - after segment, expect OPCODE again; if END_BANK -> read 4 bytes (packed CRC), verify against bank data; then unpack bank_id and bank_version from the FIRST TWO <i4 of the bank payload
      - TO_BE_CONTD: skip 5 trailing bytes (per C)
    """
    def __init__(self, block_reader):
        # block_reader is iterable of (block_idx, payload)
        self._stream = _ByteStream(iter(block_reader))

    def __iter__(self) -> Iterator[Bank]:
        started = False
        bank_buf = bytearray()

        while True:
            # Seek OPCODE; C skips bytes until it finds 0x60
            b = self._stream.read1()
            if b is None:
                return
            if b != OPCODE:
                continue  # skip stray byte, like the C does

            verb = self._stream.read1()
            if verb is None:
                return

            # ---- block-level control
            if verb == START_BLOCK:
                # next <i4 is the block number; the C reads/ignores it here
                _blkno = self._stream.read_u32le()
                continue

            if verb == END_BLOCK_LOGICAL or verb == END_BLOCK_PHYSICAL:
                # C triggers dst_get_block_; in our concatenated view just continue
                continue

            if verb == FILLER:
                # meaningless filler (no payload to skip besides the verb)
                continue

            # ---- bank-level control
            if verb == START_BANK:
                # if already started, C warns & resets; we reset cleanly
                started = True
                bank_buf.clear()
                # fall through to read segment

            elif verb == CONTINUE:
                # if not started, C warns; we'll just treat as “start a bank buffer”
                if not started:
                    started = True
                    bank_buf.clear()
                # fall through to read segment

            elif verb == TO_BE_CONTD:
                # C does: dst_nbyt += 5; finished = 0
                skip = self._stream.read_exact(5)
                if skip is None:
                    return
                # keep collecting in same bank
                continue

            elif verb == END_BANK:
                # after END_BANK, C unpacks a 4-byte CRC (always present),
                # compares it to crc_ccitt over the bank bytes
                crc_word = self._stream.read_u32le()
                if crc_word is None:
                    return
                crc_expected = crc_word & 0xFFFF
                crc_actual = crc_ccitt(bank_buf)
                if (crc_actual & 0xFFFF) != crc_expected:
                    raise ValueError(
                        f"Bank CRC mismatch: expected {crc_expected:#06x}, got {crc_actual:#06x}"
                    )

                # Now extract bank_id and bank_version from the *start* of the bank payload
                if len(bank_buf) < 8:
                    raise ValueError("Bank too short to contain id+version")
                bank_id, bank_ver = struct.unpack_from("<II", bank_buf, 0)

                yield Bank(bank_id=bank_id, bank_version=bank_ver, data=bytes(bank_buf))
                started = False
                bank_buf.clear()
                continue

            else:
                # unknown verb; C warns and skips
                continue

            # If we’re here, we need to read a bank segment (START_BANK or CONTINUE case)
            seg_len = self._stream.read_u32le()
            if seg_len is None:
                return
            seg = self._stream.read_exact(seg_len)
            if seg is None:
                return
            bank_buf += seg

            # After segment, the C immediately expects another OPCODE byte next;
            # We do not consume it here; the loop continues and will verify it naturally.

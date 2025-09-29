# src/dst2ak/opscanner.py
from __future__ import annotations
import struct
import sys
from dst2ak.blockreader import BlockReader

OPCODE = 0x60

def scan_opcodes(reader, max_ops: int = 200):
    count = 0
    for _, payload in reader:
        i = 0
        while i < len(payload) and count < max_ops:
            b = payload[i]
            i += 1
            if b != OPCODE:
                continue
            if i >= len(payload):
                break
            verb = payload[i]
            i += 1
            print(f"OP {count:04d}: verb={verb}")
            count += 1
            # Try to skip over common forms just to avoid infinite loops
            if verb == 97:      # START_BLOCK
                i += 4
            elif verb in (7, 8):  # START_BANK / CONTINUE
                if i + 4 > len(payload):
                    break
                seg_len = struct.unpack_from("<I", payload, i)[0]
                i += 4 + seg_len
            elif verb == 15:    # TO_BE_CONTD
                i += 5
            elif verb == 14:    # END_BANK
                i += 4

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m dst2ak.opscanner FILE [N_OPS]")
        sys.exit(1)
    path = sys.argv[1]
    max_ops = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    with BlockReader(path) as br:
        scan_opcodes(br, max_ops=max_ops)

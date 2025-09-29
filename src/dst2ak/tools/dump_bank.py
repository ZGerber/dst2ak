#!/usr/bin/env python3
"""
dump_banks.py

Iterate through all banks in a DST file, print their IDs, names, versions,
and payload sizes. Hex-dump the first 128 bytes of each bank.
"""

from pathlib import Path
from binascii import hexlify
import tomllib

from dst2ak.blockreader import BlockReader
from dst2ak.bankassembler import BankAssembler


def load_container_map():
    """Load containers.toml and return {bank_id: name} mapping."""
    proj_root = Path(__file__).resolve().parents[3]  # repo root
    containers = proj_root / "config" / "containers.toml"
    if not containers.exists():
        raise FileNotFoundError(f"Missing containers.toml at {containers}")
    with containers.open("rb") as f:
        data = tomllib.load(f)

    id_to_name = data.get("banks", {}).get("id_to_name", {})
    bank_map = {}
    for k, v in id_to_name.items():
        try:
            bank_id = int(k, 0)  # handles 1400000023 or "0x1234"
        except ValueError:
            continue
        bank_map[bank_id] = v
    return bank_map



def main(path: str):
    dst_path = Path(path)
    if not dst_path.exists():
        raise FileNotFoundError(f"File not found: {dst_path}")

    bank_map = load_container_map()

    with BlockReader(str(dst_path)) as br:
        for i, bank in enumerate(BankAssembler(br), start=1):
            name = bank_map.get(bank.bank_id, "UNKNOWN")
            print(f"\nBank #{i}")
            print(f"  ID: {bank.bank_id} ({name})")
            print(f"  Version: {bank.bank_version}")
            print(f"  Payload size: {len(bank.data)} bytes")
            print(f"  First 128 bytes (hex): {hexlify(bank.data[:128]).decode()}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: dump_banks.py <dst_file>")
        sys.exit(1)
    main(sys.argv[1])

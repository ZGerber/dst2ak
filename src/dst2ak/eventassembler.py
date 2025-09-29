# eventassembler.py
from dataclasses import dataclass
from typing import List, Iterator
import tomllib

from .blockreader import BlockReader
from .bankassembler import Bank, BankAssembler


@dataclass
class Event:
    banks: List[Bank]


class EventAssembler:
    """
    Groups banks into events based on start/stop bank IDs.
    Can preserve or strip the start/stop markers.
    """

    def __init__(self, br: BlockReader, toml_path: str = "containers.toml", keep_markers: bool = False):
        self.br = br
        self.keep_markers = keep_markers

        # Load config from TOML
        with open(toml_path, "rb") as fh:
            cfg = tomllib.load(fh)
        self.start_id = cfg["event_markers"]["start"]
        self.stop_id = cfg["event_markers"]["stop"]
        self.id_to_name = {int(k): v for k, v in cfg["banks"]["id_to_name"].items()}

    def __iter__(self) -> Iterator[Event]:
        return self._iter_events()

    def _iter_events(self) -> Iterator[Event]:
        in_event = False
        banks: List[Bank] = []

        for bank in BankAssembler(self.br):
            if bank.bank_id == self.start_id:
                # close unfinished event if we somehow missed stop
                if in_event and banks:
                    yield Event(banks)
                in_event = True
                banks = [bank] if self.keep_markers else []
                continue

            if bank.bank_id == self.stop_id:
                if in_event:
                    if self.keep_markers:
                        banks.append(bank)
                    yield Event(banks)
                in_event = False
                banks = []
                continue

            if in_event:
                banks.append(bank)

    def describe_bank(self, bank: Bank) -> str:
        name = self.id_to_name.get(bank.bank_id, f"UNKNOWN({bank.bank_id})")
        return f"<Bank {name} id={bank.bank_id} v={bank.bank_version} size={len(bank.data)}>"

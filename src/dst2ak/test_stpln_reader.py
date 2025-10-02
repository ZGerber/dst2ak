#!/usr/bin/env python3
from pathlib import Path
from dst2ak.blockreader import BlockReader
from dst2ak.bankassembler import BankAssembler
from dst2ak.recipe_loader import load_recipe
from dst2ak.recipe_reader import interpret_recipe

STPLN_BANKID = 15043

def main():
    recipe_path = Path("config/recipes/stpln_dst.recipe.toml")
    stpln_ops = load_recipe(recipe_path)

    dst_file = Path("MDSD_241010.tmatch.dst.gz")
    with BlockReader(str(dst_file)) as br:
        for bank in BankAssembler(br):
            if bank.bank_id == STPLN_BANKID:
                decoded = interpret_recipe(bank.data, stpln_ops)
                print("Decoded stpln:", decoded)

if __name__ == "__main__":
    main()

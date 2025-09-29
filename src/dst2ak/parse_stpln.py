#!/usr/bin/env python3
"""
Parse stpln_dst.c to generate a recipe TOML.
Only handles stpln for now (proof of concept).
"""

import re
import sys
import tomllib
import tomli_w
from pathlib import Path

# Map suffix → type
TYPE_MAP = {
    "i2": "i16",
    "i4": "i32",
    "i4asi2": "i32",
    "i4asui2": "u32",
    "r4": "f32",
    "r8": "f64",
}

CALL_RE = re.compile(
    r"dst_(pack|unpack)(i2|i4|i4asi2|i4asui2|r4|r8)_\s*\(\s*([^,]+),\s*\(([^)]+)\),"
)

def clean_field(expr: str) -> str:
    """Convert &stpln_.jday[i] → jday"""
    expr = expr.strip()
    expr = expr.lstrip("&*(").rstrip(")")
    if "." in expr:
        expr = expr.split(".")[1]
    expr = re.sub(r"\[.*?\]", "", expr)
    return expr.strip()

def parse_ops(text: str):
    ops = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = CALL_RE.search(line)
        if not m:
            continue
        action, suffix, arg, nobj = m.groups()
        field = clean_field(arg)
        typ = TYPE_MAP.get(suffix, "unknown")

        # condition = any `if (...)` above this line
        cond = ""
        for back in range(1, 4):
            if i - back >= 0 and "if" in lines[i - back]:
                cond = lines[i - back].strip()
                break

        ops.append({
            "field": field,
            "type": typ,
            "count": nobj.strip(),
            "condition": cond,
        })
    return ops

def load_schema(schema_path: Path):
    with open(schema_path, "rb") as f:
        schema = tomllib.load(f)
    out = {}
    for bank in schema.get("banks", {}).values():
        for fld in bank.get("fields", []):
            out[fld["name"]] = fld["type"]
    return out

def reconcile_types(ops, schema_types):
    for op in ops:
        if op["type"] == "unknown" and op["field"] in schema_types:
            op["type"] = schema_types[op["field"]]
    return ops

def main():
    if len(sys.argv) != 2:
        print("Usage: parse_stpln_recipe.py path/to/stpln_dst.c")
        sys.exit(1)

    src = Path(sys.argv[1])
    text = src.read_text()

    schema_path = Path("config/banks/stpln_dst_common.toml")
    schema_types = load_schema(schema_path)

    ops = parse_ops(text)
    ops = reconcile_types(ops, schema_types)

    out = { "recipes.stpln_dst": { "ops": ops } }

    out_path = Path("recipes") / "stpln_dst.toml"
    out_path.parent.mkdir(exist_ok=True, parents=True)
    with open(out_path, "wb") as f:
        tomli_w.dump(out, f)

    print(f"✔ wrote recipe → {out_path}")

if __name__ == "__main__":
    main()

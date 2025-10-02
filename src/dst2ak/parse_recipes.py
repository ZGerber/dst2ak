#!/usr/bin/env python3
"""
parse_recipes.py

Parse *_dst.c bank source files with libclang to extract pack/unpack recipes,
cross-reference with schema TOML, and emit recipe TOML.
"""

import sys
import tomllib
import tomli_w
import os
from pathlib import Path
from clang.cindex import Index, CursorKind, Config
from dst2ak import _auto_set_libclang

# Map suffix to type
TYPE_MAP = {
    "i2": "i16",
    "i4": "i32",
    "i4asi2": "i32",    # packed as i2 but stored as i4
    "i4asui2": "u32",   # unsigned
    "r4": "f32",
    "r8": "f64",
}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "config" / "schemas"

# ---------------------------------------------------------------------------
# _config_path = os.environ.get("LIBCLANG_PATH", "/usr/lib/llvm-10/lib/libclang.so")
# Config.set_library_file(_config_path)

def load_schema(bank_name: str) -> dict:
    """Load schema TOML for a given bank if available."""
    schema_path = SCHEMA_DIR / f"{bank_name}.toml"
    if not schema_path.exists():
        return {}
    with open(schema_path, "rb") as f:
        return tomllib.load(f)

def clean_field(name: str) -> str:
    """Normalize field names from C AST to clean TOML identifiers."""
    name = name.replace("&", "")
    # strip indices like [i], [ieye]
    while "[" in name and "]" in name:
        l = name.find("[")
        r = name.find("]", l)
        if r > l:
            name = name[:l] + name[r + 1 :]
        else:
            break
    # strip trailing characters
    return name.strip().rstrip(");, ")

def collect_if_conditions(node) -> list[str]:
    """Walk up the AST and collect enclosing if-statement conditions."""
    conds = []
    parent = node.semantic_parent
    while parent is not None:
        if parent.kind == CursorKind.IF_STMT:
            cond_text = " ".join(t.spelling for t in parent.get_tokens())
            conds.append(cond_text)
        parent = parent.semantic_parent
    return conds[::-1]  # outermost first

def invert_guard(cond: str) -> str:
    """Invert a guard-continue condition into a positive condition."""
    if "!=" in cond and "continue" in cond:
        expr = cond.split("!=")[0].strip()
        return f"{expr} == 1"
    return cond

def resolve_nobj_from_scope(assigns: dict, varname: str):
    """Return what nobj was set to."""
    return assigns.get(varname)

# ---------------------------------------------------------------------------

def parse_file(path: Path, bank_name: str) -> list[dict]:
    """Parse a .c file and return unpack operations as dicts."""
    index = Index.create()
    tu = index.parse(str(path), args=["-I/usr/include", "-I/usr/lib/clang/15/include"])
    ops = []

    # Track assignments like nobj = stpln_.ntube
    assigns = {}

    for c in tu.cursor.walk_preorder():
        if c.kind == CursorKind.BINARY_OPERATOR:
            toks = [t.spelling for t in c.get_tokens()]
            if "=" in toks:
                lhs, rhs = "".join(toks).split("=", 1)
                lhs, rhs = lhs.strip(), rhs.strip(";")
                assigns[lhs] = rhs

        if c.kind == CursorKind.CALL_EXPR and c.spelling.startswith("dst_unpack"):
            toks = [t.spelling for t in c.get_tokens()]
            func = toks[0]
            # collect args
            args = [t.spelling for t in c.get_arguments()]
            if not args:
                continue
            field = clean_field(args[0])
            nobj = resolve_nobj_from_scope(assigns, "nobj")

            # collect enclosing ifs
            conds = collect_if_conditions(c)
            # adjust for guard-continues
            conds = [invert_guard(cc) for cc in conds]

            ops.append(
                {
                    "func": func,
                    "field": field,
                    "nobj": nobj,
                    "conds": conds,
                }
            )

    return ops

def merge_ops(ops: list[dict], schema: dict) -> list[dict]:
    """Merge extracted ops with schema information."""
    fields = []
    for op in ops:
        typ = TYPE_MAP.get(op["func"].replace("dst_unpack", ""), "raw")
        field_entry = {
            "field": op["field"],
            "type": typ,
        }
        # preserve dynamic nobj if it's not a plain int
        if op["nobj"] is not None:
            try:
                int(op["nobj"])
                # if it's an int, store as number
                field_entry["count"] = int(op["nobj"])
            except ValueError:
                # keep expression like stpln_.ntube
                field_entry["count"] = op["nobj"]
        else:
            # fallback: use schema dims if available
            dims = schema.get("dims")
            if dims:
                field_entry["count"] = dims[-1]

        if op["conds"]:
            field_entry["cond"] = " and ".join(op["conds"])

        fields.append(field_entry)
    return fields

# ---------------------------------------------------------------------------

def main(src_file: str):
    bank_name = Path(src_file).stem.replace("_dst", "")
    schema = load_schema(f"{bank_name}_dst_common")
    ops = parse_file(Path(src_file), bank_name)
    merged = merge_ops(ops, schema)

    out = {"recipe": merged}
    with open(f"{bank_name}_dst.recipe.toml", "wb") as f:
        tomli_w.dump(out, f)

if __name__ == "__main__":
    _auto_set_libclang()
    if len(sys.argv) < 2:
        print("Usage: parse_recipes.py file.c")
        sys.exit(1)
    main(sys.argv[1])

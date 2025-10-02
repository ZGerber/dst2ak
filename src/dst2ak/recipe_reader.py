#!/usr/bin/env python3
"""
recipe_reader.py

Interpret a bank's byte payload according to a recipe TOML.
"""

import struct

# Map recipe func → struct format (little endian)
TYPE_FMT = {
    "i4": "<i",       # 32-bit signed
    "i2asi4": "<h",   # 16-bit stored, promoted to 32
    "i4asui2": "<H",  # unsigned 16 promoted to 32
    "r4": "<f",       # 32-bit float
    "r8": "<d",       # 64-bit float
}

SIZEOF = {
    "i4": 4,
    "i2asi4": 2,
    "i4asui2": 2,
    "r4": 4,
    "r8": 8,
}


def _eval_expr(expr: str, ctx: dict) -> int:
    """Evaluate count/bound/guard expressions like '${maxeye}' or 'if_eye[ieye]==1'."""
    expr = expr.strip()
    if expr.startswith("${") and expr.endswith("}"):
        key = expr[2:-1]
        return ctx[key]
    return eval(expr, {}, ctx)


def _unpack_values(data: bytes, offset: int, func: str, count: int) -> tuple[list, int]:
    """Unpack count values of type func starting from offset."""
    fmt = TYPE_FMT[func]
    size = SIZEOF[func]
    values = []
    for _ in range(count):
        chunk = data[offset:offset+size]
        if len(chunk) < size:
            raise ValueError("Truncated data")
        val = struct.unpack(fmt, chunk)[0]
        if func == "i2asi4":
            val = int(val)
        if func == "i4asui2":
            val = int(val)
        values.append(val)
        offset += size
    return values, offset


def interpret_recipe(data: bytes, ops: list[dict]) -> dict:
    """Interpret bytes using the recipe ops with loop/guard/cond logic."""
    offset = 0
    result = {}

    for op in ops:
        if op.get("op") != "unpack":
            continue

        func = op["func"]
        field = op["field"]
        count_expr = op["count"]

        # check cond (bankversion etc.)
        if "cond" in op:
            if not _eval_expr(op["cond"], result):
                continue

        # handle loop
        if "loop" in op:
            loop_var = op["loop"]["var"]
            bound_expr = op["loop"]["bound"]
            bound = _eval_expr(bound_expr, result)
            collected = []
            for i in range(bound):
                ctx = {**result, loop_var: i}
                if "guard" in op:
                    if not _eval_expr(op["guard"], ctx):
                        continue
                count = _eval_expr(count_expr, ctx) if isinstance(count_expr, str) else int(count_expr)
                vals, offset = _unpack_values(data, offset, func, count)
                collected.append(vals if count > 1 else vals[0])
            result[field] = collected
        else:
            # no loop
            count = _eval_expr(count_expr, result) if isinstance(count_expr, str) else int(count_expr)
            vals, offset = _unpack_values(data, offset, func, count)
            result[field] = vals if count > 1 else vals[0]

    return result

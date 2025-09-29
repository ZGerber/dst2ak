#!/usr/bin/env python3
"""
parse_recipes.py  (updated)

Goal: Extract pack/unpack "recipes" from *_dst.c using libclang and emit TOML:
  recipes/<bank>_dst.toml   with [[recipes.<bank>.ops]] entries.

Improvements vs previous version:
- Resolves `&nobj` by scanning scope for the most recent `nobj = <expr>`.
- Captures real enclosing IfStmt conditions (outer → inner).
- Adds heuristic to capture guard-`continue` conditions (models as `if (!(COND))`).
- Falls back to schema dims to fill unresolved counts.
- Slightly expanded TYPE_MAP (schema still wins).
"""

from __future__ import annotations

import os
import sys
import tomllib
import tomli_w
from pathlib import Path
from collections import defaultdict

# --- libclang setup -----------------------------------------------------------
from clang.cindex import Index, CursorKind, Config

# You can override via env LIBCLANG_PATH; otherwise this path is common on LLVM 20
_config_path = os.environ.get("LIBCLANG_PATH", "/usr/lib/llvm-20/lib/libclang.so")
Config.set_library_file(_config_path)

# --- project paths ------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR   = PROJECT_ROOT / "config" / "banks"
RECIPES_DIR  = PROJECT_ROOT / "recipes"
INCLUDE_DIRS = [
    str(PROJECT_ROOT / "inc"),                         # local headers (if any)
    os.path.join(os.environ.get("DSTDIR", ""), "inc"), # DST2k/TA headers
]

# --- function-suffix → type map (fallback; schema wins) -----------------------
TYPE_MAP = {
    "i2":        "i16",
    "i4":        "i32",
    "i4asi2":    "i32",  # packed as i2 but stored as i4
    "i4asui2":   "u32",  # packed as u16 but stored as u32
    "i2asi4":    "i32",  # additional common pattern
    "i2asui4":   "u32",
    "r4":        "f32",
    "r8":        "f64",
}

# ---- helpers -----------------------------------------------------------------
def get_suffix(fn: str) -> str:
    # dst_packi4asi2_ → i4asi2 ; dst_unpackr8_ → r8
    if fn.startswith("dst_pack"):
        return fn.removeprefix("dst_pack").rstrip("_")
    if fn.startswith("dst_unpack"):
        return fn.removeprefix("dst_unpack").rstrip("_")
    return ""

def tokens_to_text(node) -> str:
    return "".join(tok.spelling for tok in node.get_tokens())

def clean_field(expr: str) -> str:
    # &stpln_.eyeid[ieye] → stpln_.eyeid → eyeid
    s = expr.strip()
    # strip address/deref and parens
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    while s and s[0] in "&*":
        s = s[1:]
    # prefer struct.member if present
    if "." in s:
        s = s.split(".", 1)[1]
    elif "->" in s:
        s = s.split("->", 1)[1]
    # drop indices
    s = s.split("[", 1)[0]
    # just the field name
    return s.strip()

def normalize_count(expr: str) -> str:
    # (nobj=3,&nobj) → 3
    # (nobj=stpln_.maxeye,&nobj) → stpln_.maxeye
    # (&nobj) or (nobj=&something) → empty (we don’t know)
    s = expr.strip()
    # peel outer parens if present
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    # split by comma: left is "nobj=XXX", right often "&nobj"
    parts = [p.strip() for p in s.split(",")]
    if not parts:
        return ""
    left = parts[0]
    if left.startswith("nobj="):
        val = left[len("nobj="):].strip()
        # drop any parens left behind
        if val.startswith("(") and val.endswith(")"):
            val = val[1:-1].strip()
        # very small normalization: remove trailing semicolon if it slipped in
        return val.rstrip(";")
    # sometimes just "&nobj" (no info)
    return ""

def collect_if_conditions(call_cursor) -> list[str]:
    """
    Walk ancestors and collect all enclosing IfStmt condition texts
    (outermost … innermost). We keep the raw condition spelling.
    """
    conds = []
    cur = call_cursor
    chain = []
    while cur is not None:
        chain.append(cur)
        cur = cur.semantic_parent

    # traverse parent→child to present conditions in code order
    for node in reversed(chain):
        if node.kind == CursorKind.IF_STMT:
            cond_text = None
            children = list(node.get_children())
            if children:
                # heuristic: first child is often the condition
                cond_text = tokens_to_text(children[0]).strip()
            if not cond_text:
                full = tokens_to_text(node)
                # try to extract inside the first "( ... )"
                l = full.find("(")
                r = full.find(")")
                if l != -1 and r != -1 and r > l:
                    cond_text = full[l+1:r].strip()
                else:
                    cond_text = full.strip()
            if cond_text:
                conds.append(f"if ({cond_text})")
    return conds

def resolve_nobj_from_scope(call_cursor) -> str | None:
    """
    Look left in the current CompoundStmt for `nobj = <expr>` before this call.
    If not found, climb to parent CompoundStmt(s) and repeat.
    """
    # Step 1: find enclosing (nearest) CompoundStmt
    cur = call_cursor.semantic_parent
    while cur and cur.kind != CursorKind.COMPOUND_STMT:
        cur = cur.semantic_parent

    def scan_compound(comp, stop_at):
        last = None
        for child in comp.get_children():
            if child == stop_at:
                break
            if child.kind == CursorKind.BINARY_OPERATOR:
                toks = [t.spelling for t in child.get_tokens()]
                # crude but robust: look for 'nobj', '=', RHS...
                if len(toks) >= 3 and toks[0] == "nobj" and toks[1] == "=":
                    rhs = "".join(toks[2:])
                    if rhs.endswith(";"):
                        rhs = rhs[:-1]
                    last = rhs.strip()
        return last

    while cur:
        found = scan_compound(cur, call_cursor)
        if found:
            return found
        # climb to outer compound
        cur = cur.semantic_parent
        while cur and cur.kind != CursorKind.COMPOUND_STMT:
            cur = cur.semantic_parent
    return None

def collect_guard_continue(call_cursor) -> list[str]:
    """
    Heuristic: detect pattern
        if (COND) continue;
        <dst_unpack* call>
    in the same compound statement. Model as 'if (!(COND))'.
    """
    guards = []
    comp = call_cursor.semantic_parent
    if not comp or comp.kind != CursorKind.COMPOUND_STMT:
        return guards

    prev = []
    for child in comp.get_children():
        if child == call_cursor:
            break
        prev.append(child)

    # scan backwards: nearest guard wins
    for node in reversed(prev):
        if node.kind == CursorKind.IF_STMT:
            ch = list(node.get_children())
            if not ch:
                continue
            cond_expr = tokens_to_text(ch[0]).strip()
            # detect 'continue' in the 'then' branch (child[1], often a CompoundStmt or a single stmt)
            then = ch[1] if len(ch) > 1 else None
            then_toks = "".join(t.spelling for t in then.get_tokens()) if then else ""
            has_continue = "continue" in then_toks and ";" in then_toks
            # If there is an 'else', don't treat as guard-continue
            has_else = (len(ch) > 2)
            if has_continue and not has_else and cond_expr:
                guards.append(f"if (!({cond_expr}))")
                break
    return guards

def load_schema_info(bank_stem: str) -> dict[str, dict]:
    """
    Load schema info from config/banks/<bank>_common.toml
      Returns: {field_name: {'type': str, 'dims': list[int]|None }}
    """
    schema_path = SCHEMA_DIR / f"{bank_stem}_common.toml"
    if not schema_path.exists():
        return {}
    with open(schema_path, "rb") as f:
        data = tomllib.load(f)

    # Support two layouts:
    # - [banks.<name>] with [[banks.<name>.fields]]
    # - flat {"fields": [...]}
    fields = []
    if "fields" in data:
        fields = data["fields"]
    else:
        banks = data.get("banks", {})
        if isinstance(banks, dict):
            for _, tbl in banks.items():
                if "fields" in tbl:
                    fields = tbl["fields"]
                    break

    info = {}
    for f in fields:
        nm  = f.get("name")
        tp  = f.get("type")
        dims = f.get("dims")
        if nm:
            info[nm] = {"type": tp, "dims": dims}
    return info

# ---- core parse --------------------------------------------------------------
def parse_with_libclang(c_path: Path, include_dirs: list[str]) -> list[dict]:
    """
    Find all dst_pack*/dst_unpack* calls and extract:
      field, count, direction, suffix, function name, conditions
    """
    args = []
    for inc in include_dirs:
        if inc:
            args.extend(["-I", inc])

    index = Index.create()
    tu = index.parse(str(c_path), args=args)
    if not tu:
        raise RuntimeError(f"libclang failed to parse: {c_path}")

    ops = []
    for cur in tu.cursor.walk_preorder():
        if cur.kind != CursorKind.CALL_EXPR:
            continue
        fn = cur.spelling or ""
        if not (fn.startswith("dst_pack") or fn.startswith("dst_unpack")):
            continue

        args_nodes = list(cur.get_arguments())
        if len(args_nodes) < 2:
            # unexpected signature; skip
            continue

        # data pointer argument (first)
        data_arg = args_nodes[0]
        field_expr = tokens_to_text(data_arg)
        field_name = clean_field(field_expr)

        # nobj argument (second)
        nobj_arg = args_nodes[1]
        nobj_expr = tokens_to_text(nobj_arg)
        count_norm = normalize_count(nobj_expr)

        # Try to resolve &nobj by scanning scope for latest assignment
        if (not count_norm) or (count_norm == "&nobj"):
            inferred = resolve_nobj_from_scope(cur)
            if inferred:
                count_norm = inferred

        direction = "pack" if fn.startswith("dst_pack") else "unpack"
        suffix = get_suffix(fn)
        type_guess = TYPE_MAP.get(suffix, "unknown")

        # Real enclosing if-conditions
        conds = collect_if_conditions(cur)
        # Add guard-continue condition if found
        conds = conds + collect_guard_continue(cur)

        ops.append({
            "field": field_name or "",
            "type_guess": type_guess,
            "count_raw": nobj_expr,
            "count": count_norm,
            "directions": {direction},
            "conds": conds,
            "fn": fn,
        })

    return ops

def merge_ops(ops: list[dict], schema_info: dict[str, dict]) -> list[dict]:
    """
    Deduplicate by field. Schema type wins. Merge counts and conditions.
    If count unresolved, fallback to schema dims (last dimension or full scalar).
    """
    merged: dict[str, dict] = defaultdict(lambda: {
        "type": "unknown",
        "dims": None,
        "counts": set(),
        "conds": [],
        "directions": set(),
        "fns": set(),
    })

    for op in ops:
        fld = op["field"]
        if not fld:
            # defensive: skip unnamed (shouldn't happen)
            continue

        info = merged[fld]

        # schema type/dims
        if fld in schema_info:
            sch = schema_info[fld]
            if sch.get("type"):
                info["type"] = sch["type"]
            if sch.get("dims") is not None:
                info["dims"] = sch["dims"]

        # else first non-unknown guess
        if info["type"] == "unknown" and op["type_guess"] != "unknown":
            info["type"] = op["type_guess"]

        # count: store normalized if present, else fallback to raw if nothing
        if op["count"]:
            info["counts"].add(op["count"])
        elif op["count_raw"] and not info["counts"]:
            info["counts"].add(op["count_raw"].strip())

        # conds: keep in appearance order, dedup preserving order
        for c in op["conds"]:
            if c and c not in info["conds"]:
                info["conds"].append(c)

        info["directions"].update(op["directions"])
        info["fns"].add(op["fn"])

    # finalize list
    out = []
    for fld, inf in merged.items():
        # final count resolution
        if not inf["counts"] or inf["counts"] == {"&nobj"} or "" in inf["counts"]:
            # schema fallback: prefer last dimension if dims exist
            dims = inf.get("dims")
            if isinstance(dims, list) and len(dims) > 0:
                fallback = str(dims[-1])
                # Clean existing placeholder values
                clean_counts = {c for c in inf["counts"] if c and c != "&nobj"}
                clean_counts.add(fallback)
                counts_join = " | ".join(sorted(clean_counts)) if clean_counts else fallback
            else:
                # Nothing known
                clean_counts = {c for c in inf["counts"] if c and c != "&nobj"}
                counts_join = " | ".join(sorted(clean_counts)) if clean_counts else ""
        else:
            counts_join = " | ".join(sorted(inf["counts"]))

        out.append({
            "field": fld,
            "type": inf["type"],
            "count": counts_join,
            "cond": " && ".join(inf["conds"]) if inf["conds"] else "",
            "directions": sorted(inf["directions"]),
            # "fns": sorted(inf["fns"]),  # keep if you want to debug
        })
    # Stable sort by field for reproducibility
    out.sort(key=lambda r: r["field"])
    return out

# ---- main --------------------------------------------------------------------
def main():
    if len(sys.argv) != 2:
        print("Usage: parse_recipes.py path/to/<bank>_dst.c")
        sys.exit(1)

    c_path = Path(sys.argv[1]).resolve()
    if not c_path.exists():
        print(f"error: file not found: {c_path}")
        sys.exit(1)

    # bank stem for outputs and schema lookup: stpln_dst.c → stpln_dst
    bank_stem = c_path.stem  # "stpln_dst"
    schema_info = load_schema_info(bank_stem)

    raw_ops = parse_with_libclang(c_path, INCLUDE_DIRS)
    merged_ops = (merge_ops

                  (raw_ops, schema_info))

    # wrap in TOML
    toml_obj = {f"recipes.{bank_stem}": {"ops": merged_ops}}

    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RECIPES_DIR / f"{bank_stem}.toml"
    with open(out_path, "wb") as f:
        tomli_w.dump(toml_obj, f)

    print(f"✔ wrote recipe → {out_path}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
parse_stpln.py

Robustly extract the packed/unpacked byte order for the STPLN bank by
reading the preprocessed C for `stpln_bank_to_common_` exactly as written,
and emit a recipe TOML that mirrors the call order and dynamic counts.

Two usage modes:
  1) Preprocessed:
       python parse_stpln.py --pre /tmp/stpln_dst.pre.c \
         --schema /home/zane/software/dst2ak/src/dst2ak/config/banks/stpln_dst_common.toml \
         --out    /home/zane/software/dst2ak/src/dst2ak/config/recipes/stpln_dst.recipe.toml

  2) Original .c (this script runs clang -E):
       python parse_stpln.py --src $DSTDIR/src/bank/lib/stpln_dst.c --inc $DSTDIR/inc --out ...

This script records the dst_unpack* calls, resolves each call's Nobj,
and tags loop/guard/cond context:
  - for (ieye=0; ieye<maxeye; ++ieye) with guard if_eye[ieye]==1
  - if (bankversion>=2)

It then expands the grouped scalars (jday/jsec/msec) and (neye/nmir/ntube)
using the field order from the provided schema.
"""

from __future__ import annotations
import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# ---------------------- I/O helpers ----------------------

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")

def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

# ---------------------- Preprocess ----------------------

def preprocess(src: Path, inc: Path) -> str:
    cmd = ["clang", "-E", "-I", str(inc), str(src)]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        print("Preprocess failed:\n", e.output.decode("utf-8", "ignore"), file=sys.stderr)
        raise
    return out.decode("utf-8", "ignore")

# ---------------------- Extract function body ----------------------

def extract_function_unit(src_text: str, func_name: str) -> str:
    m = re.search(rf'\b{re.escape(func_name)}\s*\([^)]*\)\s*\{{', src_text)
    if not m:
        raise RuntimeError(f"Could not locate definition of {func_name}")
    start = m.start()
    i = src_text.find("{", m.end()-1)
    depth = 0
    end = None
    for j in range(i, len(src_text)):
        ch = src_text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end is None:
        raise RuntimeError(f"Unbalanced braces for {func_name}")
    return src_text[start:end]

# ---------------------- Parsing utilities ----------------------

CALL_PAT = re.compile(r'(dst_unpack\w+_\s*\((?:[^()]*|\([^()]*\))*\))', re.DOTALL)

def find_calls(func_text: str):
    return [(m.start(), m.group(1)) for m in CALL_PAT.finditer(func_text)]

def split_top_level_args(arg_str: str) -> list[str]:
    args, depth, cur = [], 0, []
    for ch in arg_str:
        if ch == "(":
            depth += 1; cur.append(ch)
        elif ch == ")":
            depth -= 1; cur.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
    if cur:
        args.append("".join(cur).strip())
    return args

def parse_call(call_src: str) -> dict:
    func = re.match(r'(dst_unpack\w+_)', call_src).group(1)
    inside = call_src[call_src.find("(")+1 : call_src.rfind(")")]
    args = split_top_level_args(inside)
    dest = args[0] if args else ""
    inline = None
    if len(args) > 1:
        mm = re.search(r'nobj\s*=\s*([^,\)]+)', args[1])
        if mm:
            inline = mm.group(1).strip()
    if "&bankid" in dest or "bankid" in dest:
        field = "bankid"
    elif "&bankversion" in dest or "bankversion" in dest:
        field = "bankversion"
    else:
        m = re.search(r'stpln_\.\s*([A-Za-z_]\w*)', dest)
        field = m.group(1) if m else dest
    suf = func[len("dst_unpack"):-1]
    return {"func": suf, "field": field, "inline_count": inline, "raw": call_src}

def scan_nobj_assignments_outside_calls(func_text: str):
    """Return ordered (pos, rhs) for 'nobj = ...;' seen at top-level (not inside parentheses)."""
    res, depth, i, L = [], 0, 0, len(func_text)
    while i < L:
        ch = func_text[i]
        if ch == "(":
            depth += 1; i += 1; continue
        if ch == ")":
            depth -= 1; i += 1; continue
        if depth == 0 and func_text.startswith("nobj", i):
            j = i + 4
            while j < L and func_text[j].isspace(): j += 1
            if j < L and func_text[j] == "=":
                j += 1
                while j < L and func_text[j].isspace(): j += 1
                k = j
                while k < L and func_text[k] != ";": k += 1
                rhs = func_text[j:k].strip()
                res.append((i, rhs))
                i = k + 1
                continue
        i += 1
    return res

def locate_block(src: str, head_regex: str):
    m = re.search(head_regex, src)
    if not m:
        return None
    start_br = src.find("{", m.end()-1)
    depth = 0
    for j in range(start_br, len(src)):
        ch = src[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (m.start(), j + 1)
    return None

# ---------------------- Schema helpers ----------------------

def load_schema_fields(schema_path: Path) -> list[str]:
    data = tomllib.loads(read_text(schema_path))
    for _, bdef in data.get("banks", {}).items():
        if "fields" in bdef:
            return [f["name"] for f in bdef["fields"]]
    raise ValueError("No [banks.*.fields] found in schema")

def expand_grouped_scalars(ops: list[dict], schema_fields: list[str]) -> list[dict]:
    out = []
    for e in ops:
        if e["field"] in ("jday", "neye"):
            cnt = e.get("count", "1")
            if cnt.isdigit() and int(cnt) >= 2 and "loop" not in e:
                start = schema_fields.index(e["field"])
                names = schema_fields[start : start + int(cnt)]
                for nm in names:
                    e2 = dict(e); e2["field"] = nm; e2["count"] = "1"
                    out.append(e2)
                continue
        out.append(e)
    return out

# ---------------------- Recipe builder ----------------------

def build_recipe(func_text: str, schema_fields: list[str]) -> list[dict]:
    calls = find_calls(func_text)
    assign_hist = scan_nobj_assignments_outside_calls(func_text)

    loop_block = locate_block(func_text, r'for\s*\(\s*ieye\s*=\s*0\s*;\s*ieye\s*<\s*stpln_\.\s*maxeye\s*;\s*\+\+\s*ieye\s*\)\s*\{')
    ver2_block = locate_block(func_text, r'if\s*\(\s*bankversion\s*>=\s*2\s*\)\s*\{')

    ops = []
    for pos, callsrc in calls:
        info = parse_call(callsrc)
        if info["inline_count"] is not None:
            count = info["inline_count"]
        else:
            prior = None
            for p, rhs in assign_hist:
                if p < pos: prior = rhs
                else: break
            count = prior or "1"
        count_norm = (count
                      .replace("stpln_.maxeye", "${maxeye}")
                      .replace("stpln_.nmir",   "${nmir}")
                      .replace("stpln_.ntube",  "${ntube}"))
        op = {
            "op": "unpack",
            "func": info["func"],
            "field": info["field"],
            "count": count_norm,
        }
        if loop_block and loop_block[0] <= pos < loop_block[1]:
            op["loop"]  = {"var": "ieye", "bound": "${maxeye}"}
            op["guard"] = "if_eye[ieye]==1"
        if ver2_block and ver2_block[0] <= pos < ver2_block[1]:
            op["cond"] = "bankversion>=2"
        ops.append(op)

    ops = expand_grouped_scalars(ops, schema_fields)
    return ops

# ---------------------- TOML dump ----------------------

def toml_quote(s: str) -> str:
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'

def inline_table_any(d: dict) -> str:
    return "{ " + ", ".join(f'{k} = {toml_quote(str(v))}' for k, v in d.items()) + " }"

def inline_table(d: dict) -> str:
    parts = []
    for k in ("op","func","field","count","loop","guard","cond"):
        if k not in d:
            continue
        v = d[k]
        if isinstance(v, dict):
            parts.append(f'{k} = {inline_table_any(v)}')
        else:
            parts.append(f'{k} = {toml_quote(str(v))}')
    return "{ " + ", ".join(parts) + " }"

def dump_recipe_toml(ops: list[dict]) -> str:
    lines = ["ops = ["]
    for e in ops:
        lines.append("    " + inline_table(e) + ",")
    lines.append("]")
    return "\n".join(lines) + "\n"

# ---------------------- CLI ----------------------

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pre", type=Path, help="Preprocessed C (clang -E) with stpln_bank_to_common_")
    g.add_argument("--src", type=Path, help="Original stpln_dst.c")
    ap.add_argument("--inc", type=Path, help="Include dir when using --src (e.g. $DSTDIR/inc)")
    ap.add_argument("--schema", type=Path, required=True, help="Path to stpln_dst_common.toml (schema)")
    ap.add_argument("--out", type=Path, required=True, help="Output recipe TOML path")
    args = ap.parse_args()

    if args.pre:
        src_text = read_text(args.pre)
    else:
        if not args.inc:
            ap.error("--inc is required when using --src")
        src_text = preprocess(args.src, args.inc)

    func_text = extract_function_unit(src_text, "stpln_bank_to_common_")
    schema_fields = load_schema_fields(args.schema)
    ops = build_recipe(func_text, schema_fields)
    toml_text = dump_recipe_toml(ops)
    write_text(args.out, toml_text)
    print(f"Wrote {args.out} with {len(ops)} ops")

if __name__ == "__main__":
    main()

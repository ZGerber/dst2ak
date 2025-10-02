#!/usr/bin/env python3
"""
Parse *_dst.h headers in an inc/ directory and generate TOML schemas.
Expands array dimensions using macros when possible.
"""

import os
import sys
import argparse
from pathlib import Path
from clang.cindex import Index, Config, CursorKind, TypeKind
from dst2ak import _auto_set_libclang


C_TO_SCHEMA = {
    "int": "i32",
    "short": "i16",
    "char": "i8",
    "long long": "i64",
    "float": "f32",
    "double": "f64",
}


def collect_macros(index, header, inc_dir):
    tu = index.parse(header, args=[f"-I{inc_dir}"])
    macros = {}
    for c in tu.cursor.get_children():
        if c.kind == CursorKind.MACRO_DEFINITION:
            tokens = [t.spelling for t in c.get_tokens()]
            if len(tokens) >= 2:
                name, *rest = tokens
                if rest:
                    val = rest[-1]
                    try:
                        macros[name] = int(val, 0)  # handle hex/dec
                    except ValueError:
                        macros[name] = val
    return macros


def build_typedef_map(index, dst_types, inc_dir):
    tu = index.parse(dst_types, args=[f"-I{inc_dir}"])
    typedefs = {}
    for c in tu.cursor.get_children():
        if c.kind == CursorKind.TYPEDEF_DECL:
            typedef_name = c.spelling
            underlying = c.underlying_typedef_type.spelling
            schema_type = C_TO_SCHEMA.get(underlying, underlying)
            typedefs[typedef_name] = schema_type
    return typedefs


def walk_struct(cursor, typedefs, macros):
    fields = []
    for c in cursor.get_children():
        if c.kind == CursorKind.FIELD_DECL:
            base_type = typedefs.get(c.type.spelling, c.type.spelling)
            name = c.spelling

            dims = []
            t = c.type
            while t.kind == TypeKind.CONSTANTARRAY:
                size = t.get_array_size()
                if size == -1:
                    # try to resolve macro
                    tokens = [tok.spelling for tok in c.get_tokens()]
                    for tok in tokens:
                        if tok in macros:
                            val = macros[tok]
                            if isinstance(val, int):
                                size = val
                            else:
                                print(
                                    f"Warning: unresolved macro {tok} "
                                    f"for field {name} in struct {cursor.spelling}"
                                )
                                size = tok
                            break
                dims.append(size)
                t = t.element_type

            fields.append((name, base_type, dims))
    return fields


def parse_header(header, inc_dir, dst_types):
    index = Index.create()
    macros = collect_macros(index, header, inc_dir)
    typedefs = build_typedef_map(index, dst_types, inc_dir)
    tu = index.parse(header, args=[f"-I{inc_dir}"])

    out = []
    for c in tu.cursor.get_children():
        if c.kind == CursorKind.STRUCT_DECL and c.spelling.endswith("_dst_common"):
            struct_name = c.spelling
            out.append(f"[banks.{struct_name}]")
            out.append(f'name = "{struct_name}"')
            fields = walk_struct(c, typedefs, macros)
            for name, base_type, dims in fields:
                out.append(f"[[banks.{struct_name}.fields]]")
                out.append(f'name = "{name}"')
                out.append(f'type = "{base_type}"')
                if dims:
                    out.append(f"dims = {dims}")
                out.append("")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inc", default=os.path.join(os.environ.get("DSTDIR", ""), "inc"),
                        help="Path to inc/ directory (default: $DSTDIR/inc)")
    parser.add_argument("--out", default="config/banks",
                        help="Output directory for TOML schemas (default: ./config/banks)")
    args = parser.parse_args()

    inc_dir = Path(args.inc)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    dst_types = inc_dir / "dst_std_types.h"

    headers = sorted(inc_dir.glob("*_dst.h"))
    if not headers:
        print(f"No *_dst.h headers found in {inc_dir}")
        sys.exit(1)

    for header in headers:
        toml_text = parse_header(str(header), str(inc_dir), str(dst_types))
        if not toml_text.strip():
            continue
        fname = out_dir / header.name.replace(".h", "_common.toml")
        with open(fname, "w") as f:
            f.write(toml_text)
        print(f"Wrote {fname}")


if __name__ == "__main__":
    _auto_set_libclang()
    main()

#!/usr/bin/env python3
"""
Debug parser: dump all CallExpr seen by libclang in a DST bank source file.
"""

import sys
from pathlib import Path
from clang.cindex import Index, Config, CursorKind, TranslationUnit

# Adjust if libclang is not found automatically
Config.set_library_file("/usr/lib/llvm-20/lib/libclang.so")

# Environment variables
import os
DSTDIR = os.environ.get("DSTDIR")
if not DSTDIR:
    print("❌ DSTDIR is not defined. Please export DSTDIR=/full/path/to/dst2k-ta-pro")
    sys.exit(1)

INC_DIR = Path(DSTDIR) / "inc"

def walk(cursor):
    """Recursively visit AST nodes and dump CallExprs."""
    if cursor.kind == CursorKind.CALL_EXPR:
        callee = cursor.displayname or cursor.spelling
        loc = cursor.location
        print(f"CallExpr: {callee} @ {loc.file}:{loc.line}:{loc.column}")
    for child in cursor.get_children():
        walk(child)

def main():
    if len(sys.argv) != 2:
        print("Usage: debug_calls.py path/to/bank_dst.c")
        sys.exit(1)

    src = Path(sys.argv[1])
    index = Index.create()

    tu = index.parse(
        str(src),
        args=[f"-I{INC_DIR}", "-D_GNU_SOURCE"],
        options=TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
    )

    walk(tu.cursor)

if __name__ == "__main__":
    main()

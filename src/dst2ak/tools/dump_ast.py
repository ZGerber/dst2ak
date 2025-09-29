#!/usr/bin/env python3
from clang.cindex import Index, Config
import os
import sys

# Adjust these paths
INC_DIR = "/home/zane/software/dst2k-ta-pro/inc"
Config.set_library_file("/usr/lib/llvm-20/lib/libclang.so")

def dump_ast(header_path, outdir="."):
    """Parse a header and dump its AST to a text file."""
    index = Index.create()
    tu = index.parse(header_path, args=[f"-I{INC_DIR}"])

    def walk(cursor, depth=0, lines=None):
        if lines is None:
            lines = []
        # indentation
        indent = "  " * depth
        lines.append(f"{indent}{cursor.kind} {cursor.spelling}")
        for c in cursor.get_children():
            walk(c, depth + 1, lines)
        return lines

    lines = walk(tu.cursor)

    bankname = os.path.splitext(os.path.basename(header_path))[0]
    outfile = os.path.join(outdir, f"{bankname}_ast.txt")
    with open(outfile, "w") as f:
        f.write("\n".join(lines))
    print(f"AST written to {outfile}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: dump_ast.py <header.h> [outdir]")
        sys.exit(1)
    header = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else "."
    dump_ast(header, outdir)

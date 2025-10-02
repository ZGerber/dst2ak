#!/usr/bin/env python3
"""
dump_ast.py

Utility for dumping the Clang AST of a C source/header file.
Filters out system includes so you can focus on functions defined
in the given file (e.g. stpln_dst.c).
"""

import sys
import os
from pathlib import Path

# Import dst2ak so __init__.py runs and libclang is configured
import dst2ak  # noqa: F401
from clang.cindex import Index


def walk(node, depth=0, mainfile=None, lines=None):
    """
    Recursively walk the AST and collect lines for nodes that belong
    to the main source file.
    """
    if lines is None:
        lines = []

    loc = node.location
    in_main = (
        mainfile
        and loc.file
        and os.path.samefile(str(loc.file), mainfile)
    )

    if in_main:
        indent = "  " * depth
        lines.append(
            f"{indent}{node.kind} {node.spelling} "
            f"[{loc.file}:{loc.line}]"
        )

    for c in node.get_children():
        walk(c, depth + 1, mainfile, lines)

    return lines


def dump_ast(src_file: str, outdir: str = "."):
    """
    Parse a C source/header and dump its AST to a text file.
    Only includes nodes defined in the given file.
    """
    index = Index.create()
    tu = index.parse(
        src_file,
        args=[
            f"-I{Path(src_file).parent}",  # local includes
            "-I/usr/include",
            "-I/usr/local/include",
        ],
    )

    lines = walk(tu.cursor, mainfile=os.path.realpath(src_file))

    bankname = Path(src_file).stem
    outfile = Path(outdir) / f"{bankname}_ast.txt"
    with open(outfile, "w") as f:
        f.write("\n".join(lines))

    print(f"AST written to {outfile}")


def main():
    if len(sys.argv) < 2:
        print("Usage: dump_ast.py <file.c|file.h> [outdir]")
        sys.exit(1)

    src_file = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else "."
    dump_ast(src_file, outdir)


if __name__ == "__main__":
    main()

import os
import sys
from clang.cindex import Config

def _auto_set_libclang():
    libclang_path = os.environ.get("LIBCLANG_PATH")
    if not libclang_path:
        conda_prefix = sys.prefix
        candidate = os.path.join(conda_prefix, "lib", "libclang.so")
        if os.path.exists(candidate):
            libclang_path = candidate
    if not libclang_path:
        # only raise if we actually *need* clang
        raise RuntimeError("libclang not found; set LIBCLANG_PATH manually")
    Config.set_library_file(libclang_path)

# Do NOT call here unconditionally
_auto_set_libclang()

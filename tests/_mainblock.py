"""Helper: run a module's `if __name__ == "__main__":` body in-process (so it is measured by coverage)."""
import sys
import textwrap
from pathlib import Path


def run_main_block(module, argv, **overrides):
    src = Path(module.__file__).read_text().splitlines(keepends=True)
    start = next(i for i, ln in enumerate(src) if ln.startswith("if __name__ =="))
    body = textwrap.dedent("".join(src[start + 1:]))
    code = compile("\n" * (start + 1) + body, module.__file__, "exec")   # keep original line numbers
    g = {**vars(module), "__name__": "__main__", **overrides}
    old = sys.argv
    sys.argv = [module.__file__, *argv]
    try:
        exec(code, g)
    finally:
        sys.argv = old

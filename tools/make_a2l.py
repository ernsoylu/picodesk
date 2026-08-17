#!/usr/bin/env python3
"""Generate an A2L for a workspace and patch its addresses from the ELF.

CAL-002 end to end: emit MEASUREMENT/CHARACTERISTIC entries with SYMBOL_LINK
paths, then resolve every one against the ELF's DWARF — including inner
members of nested structs — and rewrite the addresses.

Exits non-zero when any symbol stays unresolved: a placeholder address in a
shipped A2L would have the XCP master read the wrong memory.

Usage:
  tools/make_a2l.py --descriptor D.json --elf FW.elf --out FW.a2l
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "host"))

from picodesk.xcp import a2l
from picodesk.xcp.a2l_patcher import patch_a2l


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-unresolved", action="store_true",
                        help="report unresolved symbols without failing")
    args = parser.parse_args(argv)

    descriptor = json.loads(args.descriptor.read_text(encoding="utf-8"))
    items = a2l.collect_items(descriptor)

    raw = args.out.with_suffix(".raw.a2l")
    raw.write_text(a2l.render_a2l(items), encoding="utf-8")

    result = patch_a2l(args.elf, raw, args.out)
    print(f"{args.out}: {result['patched']} addresses resolved from DWARF")
    if result["unresolved"]:
        print("unresolved symbols (addresses left at placeholder):")
        for symbol in result["unresolved"]:
            print(f"  {symbol}")
        if not args.allow_unresolved:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""DWARF-aware A2L address patching (CAL-002).

Resolves symbol paths — including *inner members of nested structs*, which
is the hard part and the reason the ELF symbol table alone is not enough —
to absolute VMAs, then rewrites the ECU_ADDRESS fields of a generated A2L.

Resolution walks DWARF debug info: the top-level variable gives a base
address via its DW_AT_location exprloc (DW_OP_addr), then each dotted
component is resolved through DW_TAG_member's DW_AT_data_member_location
offset, following typedef/const/volatile wrappers and descending into
arrays by element size.

Requires the ELF built with -g (the Pico SDK default for Release keeps
debug info in the ELF; only the UF2 is stripped).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ADDR_RE = re.compile(r"^(?P<prefix>\s*ECU_ADDRESS\s+)0x[0-9A-Fa-f]+\s*$")
_VALUE_RE = re.compile(
    r"^(?P<prefix>\s*VALUE\s+)0x[0-9A-Fa-f]+(?P<suffix>\s+.*)$")
_SYMBOL_RE = re.compile(r'^\s*SYMBOL_LINK\s+"(?P<symbol>[^"]+)"')


class SymbolNotFoundError(KeyError):
    """The DWARF has no such variable or member path."""


class DwarfResolver:
    """Resolves dotted symbol paths to absolute addresses via DWARF."""

    def __init__(self, elf_path: Path) -> None:
        from elftools.elf.elffile import ELFFile

        self._fh = open(elf_path, "rb")  # noqa: SIM115 — closed in close()
        self._elf = ELFFile(self._fh)
        if not self._elf.has_dwarf_info():
            raise ValueError(
                f"{elf_path} has no DWARF info — build with -g (CAL-002)")
        self._dwarf = self._elf.get_dwarf_info()
        self._variables: dict[str, tuple[Any, int]] = {}
        self._index_variables()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> DwarfResolver:  # noqa: PYI034 — 3.9 has no Self
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- indexing ----------------------------------------------------------

    def _index_variables(self) -> None:
        """Index every statically-located variable by name.

        GCC splits some globals across two DIEs: a *declaration* carrying the
        name and type, and a *definition* carrying only the address plus a
        DW_AT_specification reference back to the declaration. Both shapes
        must be indexed, and the type lookup has to follow the reference —
        this is precisely the indirection CAL-002 calls out.
        """
        for cu in self._dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_variable":
                    continue
                location = die.attributes.get("DW_AT_location")
                if location is None:
                    continue  # declaration only; its definition carries the address
                addr = self._static_address(location)
                if addr is None:
                    continue  # not statically located (stack/register)
                name_die = self._name_source(die)
                name = name_die.attributes.get("DW_AT_name")
                if name is None:
                    continue
                key = name.value.decode("utf-8", "replace")
                # Keep the name-bearing DIE: it owns DW_AT_type.
                self._variables.setdefault(key, (name_die, addr))

    def _name_source(self, die: Any) -> Any:
        """The DIE holding this variable's name/type — itself, or the
        declaration it points at via DW_AT_specification."""
        if die.attributes.get("DW_AT_name") is not None:
            return die
        for attr in ("DW_AT_specification", "DW_AT_abstract_origin"):
            ref = die.attributes.get(attr)
            if ref is not None:
                return die.cu.get_DIE_from_refaddr(ref.value + die.cu.cu_offset)
        return die

    @staticmethod
    def _static_address(location_attr: Any) -> int | None:
        raw = location_attr.value
        if not isinstance(raw, list) or not raw or raw[0] != 0x03:
            return None  # not DW_OP_addr
        addr = 0
        for i, byte in enumerate(raw[1:5]):
            addr |= byte << (8 * i)
        return addr

    # -- type walking ------------------------------------------------------

    def _type_of(self, die: Any) -> Any | None:
        ref = die.attributes.get("DW_AT_type")
        if ref is None:
            return None
        return die.cu.get_DIE_from_refaddr(ref.value + die.cu.cu_offset)

    def _strip_wrappers(self, die: Any) -> Any:
        """Follow typedef/const/volatile to the underlying type."""
        wrappers = {"DW_TAG_typedef", "DW_TAG_const_type",
                    "DW_TAG_volatile_type", "DW_TAG_restrict_type"}
        while die is not None and die.tag in wrappers:
            die = self._type_of(die)
        return die

    def _member(self, struct_die: Any, name: str) -> tuple[Any, int]:
        for child in struct_die.iter_children():
            if child.tag != "DW_TAG_member":
                continue
            child_name = child.attributes.get("DW_AT_name")
            if child_name is None:
                continue
            if child_name.value.decode("utf-8", "replace") != name:
                continue
            offset_attr = child.attributes.get("DW_AT_data_member_location")
            offset = 0
            if offset_attr is not None:
                value = offset_attr.value
                offset = value if isinstance(value, int) else value[1]
            return child, offset
        raise SymbolNotFoundError(name)

    def resolve(self, path: str) -> int:
        """Resolve "var" or "var.member.inner" (or "var[3].member")."""
        head, _, rest = path.partition(".")
        head, index = self._split_index(head)
        entry = self._variables.get(head)
        if entry is None:
            raise SymbolNotFoundError(head)
        die, address = entry
        type_die = self._strip_wrappers(self._type_of(die))

        if index is not None:
            type_die, address = self._index_array(type_die, address, index)

        for component in filter(None, rest.split(".")):
            component, index = self._split_index(component)
            if type_die is None or type_die.tag not in (
                    "DW_TAG_structure_type", "DW_TAG_union_type"):
                raise SymbolNotFoundError(
                    f"{path}: {component} is not inside a struct")
            member_die, offset = self._member(type_die, component)
            address += offset
            type_die = self._strip_wrappers(self._type_of(member_die))
            if index is not None:
                type_die, address = self._index_array(type_die, address, index)
        return address

    def _index_array(self, type_die: Any, address: int,
                     index: int) -> tuple[Any, int]:
        if type_die is None or type_die.tag != "DW_TAG_array_type":
            raise SymbolNotFoundError("indexed a non-array symbol")
        element = self._strip_wrappers(self._type_of(type_die))
        size_attr = element.attributes.get("DW_AT_byte_size") if element else None
        size = size_attr.value if size_attr is not None else 1
        return element, address + index * size

    @staticmethod
    def _split_index(component: str) -> tuple[str, int | None]:
        match = re.match(r"^(\w+)\[(\d+)\]$", component)
        if match:
            return match.group(1), int(match.group(2))
        return component, None


def resolve_symbols(elf_path: Path, symbols: list[str]) -> dict[str, int]:
    """Resolve as many symbols as DWARF knows; missing ones are omitted."""
    resolved: dict[str, int] = {}
    with DwarfResolver(elf_path) as resolver:
        for symbol in symbols:
            try:
                resolved[symbol] = resolver.resolve(symbol)
            except (SymbolNotFoundError, ValueError):
                continue
    return resolved


def patch_a2l(elf_path: Path, a2l_path: Path, out_path: Path) -> dict[str, Any]:
    """Rewrite every address in the A2L from the ELF's DWARF (CAL-002).

    Returns {"patched": n, "unresolved": [...]}; a symbol the DWARF cannot
    resolve keeps its placeholder and is reported, never silently accepted.
    """
    lines = a2l_path.read_text(encoding="utf-8").splitlines()
    symbols = [m.group("symbol") for m in
               (_SYMBOL_RE.match(line) for line in lines) if m]
    addresses = resolve_symbols(elf_path, symbols)

    out: list[str] = []
    pending: list[int] = []   # indices of address lines awaiting a symbol
    patched = 0
    unresolved: list[str] = []

    for line in lines:
        symbol_match = _SYMBOL_RE.match(line)
        if symbol_match:
            symbol = symbol_match.group("symbol")
            address = addresses.get(symbol)
            if address is None:
                unresolved.append(symbol)
            else:
                for index in pending:
                    target = out[index]
                    addr_match = _ADDR_RE.match(target)
                    if addr_match:
                        out[index] = f"{addr_match.group('prefix')}0x{address:08X}"
                    else:
                        value_match = _VALUE_RE.match(target)
                        if value_match:
                            out[index] = (f"{value_match.group('prefix')}"
                                          f"0x{address:08X}"
                                          f"{value_match.group('suffix')}")
                    patched += 1
            pending = []
            out.append(line)
            continue

        if _ADDR_RE.match(line) or _VALUE_RE.match(line):
            pending.append(len(out))
        out.append(line)

    out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"patched": patched, "unresolved": unresolved}

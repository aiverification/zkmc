"""Parser for AutoHyper-style `.hq` HyperLTL property files.

This parser intentionally keeps the LTL body as text. The current step only
extracts the quantifier prefix and trace-indexed atoms. Formula reduction is a
separate later step.
"""

from __future__ import annotations

import re
from pathlib import Path

from .hyperltl_types import HyperAtom, HyperAtomRef, HyperFormula, TraceQuantifier


_QUANTIFIER_RE = re.compile(r"\s*(forall|exists)\s+([A-Za-z_][A-Za-z_0-9]*)\s*\.", re.IGNORECASE)
_ATOM_RE = re.compile(r"\{([^{}]*)\}", re.DOTALL)
_REF_RE = re.compile(r'"([^"]+)"_([A-Za-z_][A-Za-z_0-9]*)')


def parse_hq(text: str) -> HyperFormula:
    """Parse HyperLTL text into a lightweight HyperFormula AST."""
    source = text.strip()
    if not source:
        raise ValueError("empty HyperLTL input")

    quantifiers: list[TraceQuantifier] = []
    position = 0

    while True:
        match = _QUANTIFIER_RE.match(source, position)
        if not match:
            break
        kind = match.group(1).lower()
        trace = match.group(2)
        quantifiers.append(TraceQuantifier(kind=kind, trace=trace))
        position = match.end()

    body = source[position:].strip()
    if not body:
        raise ValueError("HyperLTL formula has no body")

    atoms = tuple(_extract_atoms(body))
    return HyperFormula(quantifiers=tuple(quantifiers), body=body, atoms=atoms)


def parse_hq_file(path: str) -> HyperFormula:
    return parse_hq(Path(path).read_text())


def _extract_atoms(body: str) -> list[HyperAtom]:
    atoms: list[HyperAtom] = []
    for match in _ATOM_RE.finditer(body):
        raw = " ".join(match.group(1).split())
        refs = tuple(
            HyperAtomRef(variable=ref.group(1), trace=ref.group(2))
            for ref in _REF_RE.finditer(raw)
        )
        atoms.append(HyperAtom(raw=raw, references=refs))
    return atoms

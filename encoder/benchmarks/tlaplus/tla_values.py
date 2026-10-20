"""Concrete TLA+ values (as TLC computes them) and a parser for TLC's state dumps.

- integers are Python `int`, booleans Python `bool` (checked before `int` everywhere),
  strings Python `str`, model values `ModelValue`;
- finite sets are `frozenset`;
- functions are `Fn` (records are functions on strings, tuples/sequences functions on 1..n);
- infinite or huge sets (`Nat`, `Seq(S)`, `[S -> T]`, ...) are `LazySet`, supporting membership only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class EvalError(Exception):
    """A TLA+ expression is undefined (TLC would report an error) or unsupported."""


@dataclass(frozen=True, order=True)
class ModelValue:
    name: str

    def __repr__(self) -> str:
        return self.name


class Fn:
    """An immutable TLA+ function with a finite domain."""

    __slots__ = ("_map", "_hash")

    def __init__(self, mapping: dict):
        self._map = dict(mapping)
        self._hash = None

    @staticmethod
    def tuple(items) -> "Fn":
        return Fn({i + 1: v for i, v in enumerate(items)})

    def __getitem__(self, k):
        try:
            return self._map[k]
        except (KeyError, TypeError):
            raise EvalError(f"function applied outside its domain: {show(k)}") from None

    def __contains__(self, k):
        return k in self._map

    def domain(self) -> frozenset:
        return frozenset(self._map)

    def items(self):
        return self._map.items()

    def keys(self):
        return self._map.keys()

    def values(self):
        return self._map.values()

    def __len__(self):
        return len(self._map)

    def is_seq(self) -> bool:
        n = len(self._map)
        return all(isinstance(k, int) and not isinstance(k, bool) and 1 <= k <= n for k in self._map)

    def seq(self) -> list:
        if not self.is_seq():
            raise EvalError(f"not a sequence: {show(self)}")
        return [self._map[i] for i in range(1, len(self._map) + 1)]

    def __eq__(self, other):
        return isinstance(other, Fn) and self._map == other._map

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(frozenset(self._map.items()))
        return self._hash

    def __repr__(self):
        return show(self)


class LazySet:
    """A set that is only ever tested for membership (Nat, Int, STRING, Seq(S), [S -> T], ...)."""

    def __init__(self, name: str, member: Callable[[Any], bool], elems: Callable[[], frozenset] | None = None):
        self.name = name
        self.member = member
        self.elems = elems  # enumerates the set if it is finite and small enough

    def __repr__(self):
        return self.name


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def sort_key(v):
    """A total order on values, used for canonical output and CHOOSE."""
    if isinstance(v, bool):
        return (0, int(v))
    if is_int(v):
        return (1, v)
    if isinstance(v, str):
        return (2, v)
    if isinstance(v, ModelValue):
        return (3, v.name)
    if isinstance(v, frozenset):
        return (4, len(v), tuple(sorted(sort_key(x) for x in v)))
    if isinstance(v, Fn):
        return (5, len(v), tuple(sorted((sort_key(k), sort_key(x)) for k, x in v.items())))
    return (6, repr(v))


def sorted_values(vs) -> list:
    return sorted(vs, key=sort_key)


def show(v) -> str:
    """TLC-style rendering of a value."""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if is_int(v):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, ModelValue):
        return v.name
    if isinstance(v, frozenset):
        return "{" + ", ".join(show(x) for x in sorted_values(v)) + "}"
    if isinstance(v, Fn):
        if v.is_seq():
            return "<<" + ", ".join(show(x) for x in v.seq()) + ">>"
        if len(v) and all(isinstance(k, str) for k in v.keys()):
            return "[" + ", ".join(f"{k} |-> {show(x)}" for k, x in sorted(v.items())) + "]"
        return "(" + " @@ ".join(f"{show(k)} :> {show(x)}" for k, x in
                                 sorted(v.items(), key=lambda kv: sort_key(kv[0]))) + ")"
    if isinstance(v, LazySet):
        return v.name
    return repr(v)


# --------------------------------------------------------------------------------------------
# TLC value syntax (state dumps)
# --------------------------------------------------------------------------------------------

class _ValueParser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def ws(self):
        s, i = self.s, self.i
        while i < len(s) and s[i] in " \t\r\n":
            i += 1
        self.i = i

    def peek(self, tok: str) -> bool:
        self.ws()
        return self.s.startswith(tok, self.i)

    def eat(self, tok: str):
        if not self.peek(tok):
            raise ValueError(f"expected {tok!r} at {self.s[self.i:self.i + 40]!r}")
        self.i += len(tok)

    def ident(self) -> str:
        self.ws()
        j = self.i
        while j < len(self.s) and (self.s[j].isalnum() or self.s[j] == "_"):
            j += 1
        if j == self.i:
            raise ValueError(f"expected identifier at {self.s[self.i:self.i + 40]!r}")
        out, self.i = self.s[self.i:j], j
        return out

    def value(self):
        self.ws()
        s, i = self.s, self.i
        c = s[i]
        if c == '"':
            j, buf = i + 1, []
            while s[j] != '"':
                if s[j] == "\\":
                    j += 1
                    buf.append({"n": "\n", "t": "\t"}.get(s[j], s[j]))
                else:
                    buf.append(s[j])
                j += 1
            self.i = j + 1
            return "".join(buf)
        if c == "-" or c.isdigit():
            j = i + 1
            while j < len(s) and s[j].isdigit():
                j += 1
            self.i = j
            n = int(s[i:j])
            if s.startswith("..", j):  # an interval a..b
                self.i = j + 2
                m = self.value()
                return frozenset(range(n, m + 1))
            return n
        if s.startswith("<<", i):
            self.i += 2
            items = self._list(">>")
            return Fn.tuple(items)
        if c == "{":
            self.i += 1
            return frozenset(self._list("}"))
        if c == "[":
            self.i += 1
            fields = {}
            if not self.peek("]"):
                while True:
                    k = self.ident()
                    self.eat("|->")
                    fields[k] = self.value()
                    if self.peek(","):
                        self.eat(",")
                        continue
                    break
            self.eat("]")
            return Fn(fields)
        if c == "(":
            self.i += 1
            mapping = {}
            while True:
                k = self.value()
                self.eat(":>")
                mapping[k] = self.value()
                if self.peek("@@"):
                    self.eat("@@")
                    continue
                break
            self.eat(")")
            return Fn(mapping)
        name = self.ident()
        if name == "TRUE":
            return True
        if name == "FALSE":
            return False
        return ModelValue(name)

    def _list(self, close: str) -> list:
        items = []
        if self.peek(close):
            self.eat(close)
            return items
        while True:
            items.append(self.value())
            if self.peek(","):
                self.eat(",")
                continue
            self.eat(close)
            return items


def parse_value(text: str):
    p = _ValueParser(text)
    v = p.value()
    p.ws()
    if p.i != len(p.s):
        raise ValueError(f"trailing input {p.s[p.i:p.i + 40]!r}")
    return v


def parse_state(text: str) -> dict:
    """`/\\ x = v /\\ y = w ...` (one TLC state) -> {var: value}."""
    out = {}
    p = _ValueParser(text.strip())
    while True:
        p.ws()
        if p.i >= len(p.s):
            return out
        if p.peek("/\\"):
            p.eat("/\\")
        name = p.ident()
        p.eat("=")
        out[name] = p.value()


def parse_dump(text: str) -> list[dict]:
    """A TLC `-dump` file -> list of states in dump order."""
    states = []
    for block in text.split("\nState ")[0:]:
        block = block.strip()
        if not block:
            continue
        if block.startswith("State "):
            block = block[len("State "):]
        _, _, body = block.partition(":\n")
        states.append(parse_state(body) if body.strip() else {})
    return states

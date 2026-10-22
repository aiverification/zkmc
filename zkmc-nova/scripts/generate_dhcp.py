#!/usr/bin/env python3
"""Generate the parameterized DHCP guarded-command benchmarks from the paper."""

from __future__ import annotations

import argparse
from pathlib import Path

OFFSETS = {1: 1, 2: 1, 3: 0, 4: -1, 5: 0}


def delay_expr(attempt: int) -> str:
    """Return the paper model's backoff delay expression for one attempt."""
    return f"2**{attempt + 1} + k{attempt}"


def sum_expr(start: int, attempts: int) -> str:
    """Return a readable sum of remaining backoff windows."""
    terms = [f"({delay_expr(index)})" for index in range(start, attempts + 1)]
    return " + ".join(terms) if terms else "0"


def generate(w1: int, attempts: int, w2: int, no_offered_state: bool) -> str:
    """Build one complete guarded-command model."""
    if w1 < 1 or w2 < 1:
        raise ValueError("wait bounds must be positive")
    if attempts not in OFFSETS:
        raise ValueError("attempts must lie in [1, 5]")

    if no_offered_state:
        states = {
            "Init": 0,
            "WaitOFF": 1,
            "WaitAN": 2,
            "TryARP": 3,
            "WaitD": 4,
            "Configured": 5,
            "Fail": 6,
        }
    else:
        states = {
            "Init": 0,
            "WaitOFF": 1,
            "Offered": 2,
            "WaitAN": 3,
            "TryARP": 4,
            "WaitD": 5,
            "Configured": 6,
            "Fail": 7,
        }

    lines = [
        "// Generated parameterized DHCP benchmark.",
        "const notReceived = 0",
        "const received = 1",
        "",
    ]
    lines.extend(f"const {name} = {value}" for name, value in states.items())
    lines.extend([
        "",
        f"const maxI = {attempts}",
        f"const k0 = {w1}",
    ])
    lines.extend(f"const k{index} = {OFFSETS[index]}" for index in range(1, attempts + 1))
    lines.extend([
        f"const kD = {w2}",
        "",
        f"type status: 0..{max(states.values())}",
        f"type i: 1..{attempts}",
        f"type delay: 1..{delay_expr(attempts)}",
        "type OFF: 0..1",
        "type ACK: 0..1",
        "type NAK: 0..1",
        "",
        "init: status = Init && ACK = notReceived && NAK = notReceived && OFF = notReceived",
        "",
        "// DHCPOFFER wait.",
        "[] status == Init -> status = WaitOFF; delay = k0",
        "[] status == WaitOFF && OFF == notReceived && delay > 1 -> delay = delay - 1",
        "[] status == WaitOFF && OFF == notReceived && delay = 1 -> status = Fail",
    ])

    receive_target = "WaitAN" if no_offered_state else "Offered"
    receive_update = f"status = {receive_target}"
    if no_offered_state:
        receive_update += f"; i = 1; delay = {delay_expr(1)}"
    lines.append(
        "[] status == WaitOFF && OFF == notReceived && delay > 0 "
        f"-> OFF = received; {receive_update}"
    )
    if not no_offered_state:
        lines.append(
            f"[] status == Offered -> status = WaitAN; i = 1; delay = {delay_expr(1)}"
        )

    lines.extend([
        "",
        "// DHCPACK/DHCPNAK exponential-backoff loop.",
        "[] status == WaitAN && ACK == notReceived && NAK == notReceived && delay > 1 -> delay = delay - 1",
    ])
    for attempt in range(1, attempts):
        lines.append(
            "[] status == WaitAN && ACK == notReceived && NAK == notReceived "
            f"&& delay == 1 && i == {attempt} -> i = i + 1; delay = {delay_expr(attempt + 1)}"
        )
    lines.extend([
        "[] status == WaitAN && ACK == notReceived && NAK == notReceived "
        f"&& delay == 1 && i == {attempts} -> status = Fail",
        "[] status == WaitAN && ACK == notReceived && NAK == notReceived && delay > 0 -> status = TryARP",
        "[] status == WaitAN && ACK == notReceived && NAK == notReceived && delay > 0 -> status = Fail",
        "",
        "// ARP check and DHCPDECLINE wait.",
        "[] status == TryARP -> status = Configured",
        "[] status == TryARP -> status = WaitD; delay = kD",
        "[] status == WaitD && delay > 1 -> delay = delay - 1",
        "[] status == WaitD && delay == 1 -> status = Fail",
        "",
        "// Property: eventually reach Fail or Configured.",
        "automaton_init: q0",
        "trans(q0, q0): true",
        "",
        "rank(q0):",
        f"    [] status == Init -> 3 + kD + {sum_expr(1, attempts)} + k0",
        f"    [] status == WaitOFF && delay > 0 && delay <= k0 -> 2 + kD + {sum_expr(1, attempts)} + delay",
    ])
    if not no_offered_state:
        lines.append(f"    [] status == Offered -> 2 + kD + {sum_expr(1, attempts)}")

    for attempt in range(1, attempts + 1):
        remaining = sum_expr(attempt + 1, attempts)
        lines.append(
            f"    [] status == WaitAN && i == {attempt} && delay >= 1 "
            f"&& delay <= {delay_expr(attempt)} -> 1 + kD + {remaining} + delay"
        )

    lines.extend([
        "    [] status == TryARP -> 1 + kD",
        "    [] status == WaitD && delay >= 1 && delay <= kD -> delay",
        "    [] status >= Configured && status <= Fail -> 0",
        "    [] status < Init -> inf",
        "    [] status > Fail -> inf",
        "    [] status == WaitOFF && delay > k0 -> inf",
        "    [] status == WaitOFF && delay < 1 -> inf",
        "    [] status == WaitD && delay > kD -> inf",
        "    [] status == WaitD && delay < 1 -> inf",
        f"    [] status == WaitAN && i >= 1 && i <= {attempts} && delay < 1 -> inf",
    ])
    for attempt in range(1, attempts + 1):
        lines.append(
            f"    [] status == WaitAN && i == {attempt} && delay > {delay_expr(attempt)} -> inf"
        )
    lines.extend([
        "    [] status == WaitAN && i < 1 -> inf",
        f"    [] status == WaitAN && i > {attempts} -> inf",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    """Write one selected DHCP model."""
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--w1", type=int, required=True)
    parser.add_argument("--attempts", type=int, required=True)
    parser.add_argument("--w2", type=int, required=True)
    parser.add_argument("--no-offered-state", action="store_true")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        generate(args.w1, args.attempts, args.w2, args.no_offered_state)
    )
    print(f"generated DHCP model: {args.output}")


if __name__ == "__main__":
    main()

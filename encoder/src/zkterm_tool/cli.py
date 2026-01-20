"""CLI for zkterm-tool: encode guarded commands as matrix inequalities."""

import argparse
import sys
from typing import TextIO

from .parser import parse
from .encoder import encode_program, TransitionEncoding


def format_encoding(enc: TransitionEncoding, index: int | None = None) -> str:
    """Format a transition encoding as readable text."""
    lines = []
    
    if index is not None:
        lines.append(f"=== Transition {index + 1} ===")
    
    full_vars = enc.full_variables()
    lines.append(f"Variables x = [{', '.join(full_vars)}]")
    
    if enc.C.shape[0] > 0:
        lines.append(f"\nNon-strict inequalities Cx ≤ c:")
        lines.append(f"C =")
        for row in enc.C:
            lines.append(f"  [{' '.join(f'{v:3d}' for v in row)}]")
        lines.append(f"c = [{' '.join(f'{v:3d}' for v in enc.c)}]")
    else:
        lines.append("\nNo non-strict inequalities")
    
    if enc.A.shape[0] > 0:
        lines.append(f"\nStrict inequalities Ax < a:")
        lines.append(f"A =")
        for row in enc.A:
            lines.append(f"  [{' '.join(f'{v:3d}' for v in row)}]")
        lines.append(f"a = [{' '.join(f'{v:3d}' for v in enc.a)}]")
    else:
        lines.append("\nNo strict inequalities")
    
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Encode guarded commands as matrix/vector inequality constraints.",
        epilog="""
Example:
  echo '[] y < z -> y = y + 1' | zkterm
  zkterm program.gc
        """
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input file with guarded commands (default: stdin)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show parsed commands"
    )
    
    args = parser.parse_args(argv)
    
    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1
        
        commands = parse(text)
        
        if args.verbose:
            print("Parsed commands:")
            for i, cmd in enumerate(commands):
                print(f"  {i + 1}. {cmd}")
            print()
        
        encodings = encode_program(commands)
        
        for i, enc in enumerate(encodings):
            if i > 0:
                print("\n")
            print(format_encoding(enc, index=i if len(encodings) > 1 else None))
        
        return 0
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

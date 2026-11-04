"""One-command entry point: balance + master the open Audacity 3 session.

    python audacity_bridge.py [--apply-gains] [--compare] [--out folder] ...

Same as `ammb audacity ...`.
"""

import sys

from ammb.cli import main

if __name__ == "__main__":
    sys.exit(main(["audacity", *sys.argv[1:]]))

"""Entry point for `python -m renovate_preview`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

"""The ouro driver: `python3 bin/ouro <command>` runs the loop's fixed sequence."""

import sys

if sys.version_info < (3, 12):
    raise SystemExit("ouro requires Python 3.12+; upgrade the invoking interpreter")

import sys
from pathlib import Path

sys.dont_write_bytecode = True
# bin/ouro itself must not sit on the module path: its modules are the package's, not top-level names.
sys.path[0] = str(Path(__file__).resolve().parent.parent)

from ouro.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))

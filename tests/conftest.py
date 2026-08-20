import sys
from pathlib import Path

# Make `import sql_guard` work without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

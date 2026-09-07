"""Start the local app without installing packages or changing the Python environment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mtga_coach.__main__ import main

if __name__ == "__main__":
    main()

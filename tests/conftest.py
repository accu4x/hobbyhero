"""Put the project root on sys.path so tests import `src.` the way the pipeline does."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

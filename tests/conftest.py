import os
import sys

# make the repo root importable so `from collectors import ...` works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

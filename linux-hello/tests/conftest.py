import os
import sys

# The python sources are not a package: make them importable as installed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

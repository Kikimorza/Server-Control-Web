#!/usr/bin/env python3
import sys

if sys.version_info < (3, 8):
    sys.exit("Нужен Python 3.8 или новее")

from scc.main import main

if __name__ == "__main__":
    sys.exit(main())

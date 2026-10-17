"""支持 ``python -m investment_attribution``。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

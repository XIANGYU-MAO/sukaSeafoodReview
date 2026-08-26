from __future__ import annotations

import sys


if sys.argv[1:]:
    from .cli import main

    raise SystemExit(main())

from .gui import main as gui_main

raise SystemExit(gui_main())

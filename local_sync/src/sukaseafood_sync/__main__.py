from __future__ import annotations

import sys


if sys.argv[1:]:
    from sukaseafood_sync.cli import main

    raise SystemExit(main())

from sukaseafood_sync.gui import main as gui_main

raise SystemExit(gui_main())

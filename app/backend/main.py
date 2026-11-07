"""Container entry point: python3 /opt/strata-supervisor/backend/main.py (this folder is put on sys.path first)."""

import sys

from strata_webui.supervisor import main

sys.exit(main())

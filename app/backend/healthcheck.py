"""Container health: the web UI answers on UI_PORT (IMPLEMENTATION.md, section 5, point 12).

Healthy without a loaded model, so no start period of hours is needed.
"""

import os
import sys
import urllib.request

port = os.environ.get("UI_PORT", "8081")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=4) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
    print(f"not healthy: {exc}", file=sys.stderr)
    sys.exit(1)

"""Export one service's OpenAPI document: `export_openapi.py <module> <out.json>`."""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path


def main() -> int:
    module, out = sys.argv[1], Path(sys.argv[2])
    factory = importlib.import_module(f"{module}.main").create_app
    kwargs = {}
    # A composing service (api-gateway) reads sibling contracts from the output dir.
    if "contracts_dir" in inspect.signature(factory).parameters:
        kwargs["contracts_dir"] = out.parent
    app = factory(**kwargs)
    out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

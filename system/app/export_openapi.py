"""Export the FastAPI OpenAPI schema for the UI contract."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .main import app


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../contract/openapi.json")
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

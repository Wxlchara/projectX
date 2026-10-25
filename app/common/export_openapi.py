"""Generate the reviewable contract without starting HTTP or initializing data."""

import argparse
import json
from pathlib import Path

from app.main import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("openapi.json"))
    arguments = parser.parse_args()
    content = json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.check:
        if not arguments.output.exists() or arguments.output.read_text(encoding="utf-8") != content:
            print("OpenAPI snapshot differs; run python -m app.common.export_openapi")
            return 1
        print("OpenAPI snapshot is current")
    else:
        arguments.output.write_text(content, encoding="utf-8")
        print(f"OpenAPI written to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Write the FastAPI OpenAPI document to disk without starting a server.

Usage: ``uv run python -m deepfix_rag.api.export_openapi [--output PATH]``
"""

import argparse
import json
from pathlib import Path

from deepfix_rag.api.app import app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[3] / "web" / "openapi.json"


def export_openapi(output: Path = DEFAULT_OUTPUT) -> Path:
    """Serialize ``app.openapi()`` to ``output`` with stable formatting."""
    output.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False)
    output.write_text(document + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(f"Wrote {export_openapi(args.output)}")


if __name__ == "__main__":
    main()

"""Write the API's OpenAPI document, from which the frontend's types are generated.

uv run python -m scripts.export_openapi ../frontend/src/lib/openapi.json
"""

import json
import sys
from pathlib import Path

from app.core.config import Settings
from app.main import create_app


def main() -> int:
    target = Path(sys.argv[1])
    # Development settings expose the schema; nothing is started or connected.
    app = create_app(Settings(_env_file=None, app_env="development"))
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

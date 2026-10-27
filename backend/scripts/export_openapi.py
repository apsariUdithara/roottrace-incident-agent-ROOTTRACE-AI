"""Write the API's OpenAPI schema to a file: the contract the frontend's types are generated from.

No server or database is needed: the schema comes straight from the app's routes and models.
The frontend runs this as the first half of `npm run gen:api`; CI runs it to check the committed
copy is current. Run from backend/:
    uv run python scripts/export_openapi.py ../frontend/openapi.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: export_openapi.py <output.json>")
    out = Path(sys.argv[1])
    text = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    # Always LF, so the file is byte-identical on Windows and on CI's Linux runner.
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {out} ({len(app.openapi()['paths'])} paths)")


if __name__ == "__main__":
    main()

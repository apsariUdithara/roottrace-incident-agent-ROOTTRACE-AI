"""Phase 0 spike: list the models this Nebius key can use.

Run: python spikes/01_list_models.py
"""
import json
import pathlib
import urllib.request

BASE_URL = "https://api.tokenfactory.nebius.com/v1"


def load_key() -> str:
    env = pathlib.Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("NEBIUS_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("NEBIUS_API_KEY not found in .env")


def main() -> None:
    req = urllib.request.Request(
        f"{BASE_URL}/models?verbose=true",
        headers={"Authorization": f"Bearer {load_key()}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        models = json.load(resp)["data"]

    ids = sorted(m["id"] for m in models)
    nvidia = [i for i in ids if "nvidia" in i.lower() or "nemotron" in i.lower()]
    embed = [i for i in ids if "embed" in i.lower() or "bge" in i.lower()]

    print(f"{len(ids)} models available\n")
    print("NVIDIA / Nemotron:")
    print("\n".join(f"  {i}" for i in nvidia) or "  (none found)")
    print("\nEmbedding candidates:")
    print("\n".join(f"  {i}" for i in embed) or "  (none found)")
    print("\nAll models:")
    print("\n".join(f"  {i}" for i in ids))


if __name__ == "__main__":
    main()

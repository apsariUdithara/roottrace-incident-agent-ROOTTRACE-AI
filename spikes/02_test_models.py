"""Phase 0 spike: can each Nemotron tier call a tool and return fixed-shape JSON?
Also measures the embedding vector size.

Makes 6 small requests (2 on Nano, 2 on Super, 2 embeddings). No Ultra.
Run: python spikes/02_test_models.py
"""
import json
import pathlib
import urllib.error
import urllib.request

BASE_URL = "https://api.tokenfactory.nebius.com/v1"
MODELS = {
    "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "lightning": "nvidia/Nemotron-3_5-Lightning",
}
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"

# What we tell the model a tool looks like: a name, a description, and the
# arguments it takes. The model never runs it; it only asks us to.
SEARCH_LOGS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_logs",
        "description": "Search structured application logs.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name, e.g. payments-api"},
                "level": {"type": "string", "enum": ["INFO", "WARN", "ERROR"]},
            },
            "required": ["service", "level"],
        },
    },
}

# The fixed shape we want answers in.
ROOT_CAUSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["root_cause", "confidence"],
    "additionalProperties": False,
}


def load_key() -> str:
    env = pathlib.Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        if line.strip().startswith("NEBIUS_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("NEBIUS_API_KEY not found in .env")


KEY = load_key()


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"}


def usage(r: dict) -> str:
    u = r.get("usage", {})
    return f"{u.get('prompt_tokens', '?')} in / {u.get('completion_tokens', '?')} out tokens"


def test_tool_call(model: str) -> str:
    r = post("/chat/completions", {
        "model": model,
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "Find the error logs for the payments-api service."}],
        "tools": [SEARCH_LOGS_TOOL],
    })
    if "error" in r:
        return f"FAIL  {r['error']}"
    calls = r["choices"][0]["message"].get("tool_calls") or []
    if not calls:
        return f"FAIL  no tool call; model said: {str(r['choices'][0]['message'].get('content'))[:120]!r}  ({usage(r)})"
    fn = calls[0]["function"]
    args = json.loads(fn["arguments"])  # raises if the arguments are not valid JSON
    ok = fn["name"] == "search_logs" and args.get("service") and args.get("level") == "ERROR"
    return f"{'PASS' if ok else 'FAIL'}  {fn['name']}({args})  ({usage(r)})"


def test_structured_output(model: str) -> str:
    r = post("/chat/completions", {
        "model": model,
        "max_tokens": 400,
        "messages": [{"role": "user", "content":
                      "Logs show 'too many connections' right after the DB pool size was raised from 20 to 100. "
                      "Give the root cause and your confidence from 0 to 1."}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "root_cause", "schema": ROOT_CAUSE_SCHEMA, "strict": True}},
    })
    if "error" in r:
        return f"FAIL  {r['error']}"
    text = r["choices"][0]["message"].get("content") or ""
    try:
        data = json.loads(text)
        ok = isinstance(data.get("root_cause"), str) and isinstance(data.get("confidence"), (int, float))
        return f"{'PASS' if ok else 'FAIL'}  {data}  ({usage(r)})"
    except json.JSONDecodeError:
        return f"FAIL  not JSON: {text[:120]!r}  finish={r['choices'][0].get('finish_reason')}  ({usage(r)})"


def test_embedding(dimensions: int | None) -> str:
    body = {"model": EMBED_MODEL, "input": "connection pool exhausted"}
    if dimensions:
        body["dimensions"] = dimensions
    r = post("/embeddings", body)
    if "error" in r:
        return f"FAIL  {r['error']}"
    return f"vector size {len(r['data'][0]['embedding'])}  ({usage(r)})"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console is cp1252
    for tier, model in MODELS.items():
        print(f"\n{tier.upper()}  {model}")
        print("  tool call:        ", test_tool_call(model))
        print("  structured output:", test_structured_output(model))
    print(f"\nEMBEDDINGS  {EMBED_MODEL}")
    print("  default size:     ", test_embedding(None))
    print("  asked for 1024:   ", test_embedding(1024))

"""What each model costs, from the Nebius Token Factory model pages (checked 2026-10-11)."""

# (input, output) in US dollars per 1 million tokens.
PRICES_PER_MILLION = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
    "Qwen/Qwen3-Embedding-8B": (0.01, 0.00),
}


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Dollar cost of one call: tokens sent x input price + tokens received x output price."""
    price_in, price_out = PRICES_PER_MILLION[model]
    return prompt_tokens * price_in / 1_000_000 + completion_tokens * price_out / 1_000_000

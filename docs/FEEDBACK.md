# Feedback: Nebius Token Factory, Sandboxes and NVIDIA Nemotron

What we learned building RootTrace AI (11–30 Oct 2026). Every point comes from something we ran into; the scripts that confirmed them are in `spikes/` and `eval/`.

## What worked well

- **OpenAI-compatible API.** We used the standard `openai` Python SDK with only a different `base_url`. Tool calling and `json_schema` structured output worked on Nemotron 3 Nano and Super on the first try (`spikes/02_test_models.py`), and Ultra handled every hypothesis, verification and report step in our evaluation.
- **Three model sizes, one API.** Being able to route by task (Nano for triage and reranking, Super for the tool loop and patches, Ultra for reasoning) is what kept us inside the free credits. A full investigation costs about **$0.05–0.08**, and our 18-case evaluation cost **$1.23** in total.
- **Embeddings with a size option.** `Qwen/Qwen3-Embedding-8B` returns 4096 dimensions by default but honours `dimensions: 1024`. That kept us under pgvector's 2000-dimension index limit without a second model.
- **Sandboxes give us the red→green proof.** The ConTree CLI let us copy a repo snapshot in, run pytest, and throw the sandbox away. We build one image with the project's dependencies and tag it, so later runs start fast. Every fix in our evaluation was tested this way: the bug reproduces first (red), and the patched code passes (green). Free usage during the beta made this possible on a student budget.
- **The public `python:3.12` image includes git**, so we didn't need a custom base image for patching.

## What slowed us down

1. **Sandbox access took several days.** Sandboxes were disabled on our project until we filled in a request form; access came through around 15 Oct. For a two-week hackathon that's a big share of the time. Suggestion: enable Sandboxes automatically for hackathon projects, or say on the hackathon page that a request is needed.
2. **Short outages (HTTP 503).** On 24 Oct, three investigations in one evaluation run failed with `503 Service Temporarily Unavailable` from nginx, after retries over about 40 seconds. We now back off for up to about 4 minutes. A status page, or a `Retry-After` header on 503s, would help clients choose a sensible wait.
3. **Switching reasoning off isn't documented.** We couldn't find a confirmed way to turn Nemotron's thinking off per request. A documented parameter would let cheap steps (classification, reranking) skip it and save tokens.
4. **Python SDK vs CLI.** We couldn't find the SDK equivalent of the CLI's `run --file local:remote`, so we call the CLI from Python. A short "upload a folder and run a command" SDK example would help.
5. **`Nemotron-3_5-Lightning` and structured output.** Lightning is about 5× faster at Nano's price, and tool calling worked. With `json_schema`, though, it wrote its reasoning into the answer and ran out of tokens, so we couldn't use it for structured steps. A note on which models support `json_schema` would save testing time.
6. **Model IDs are case-sensitive** (`nvidia/Nemotron-3-Ultra-550b-a55b` vs `nvidia/nemotron-3-super-120b-a12b`). Guessed lower-case IDs fail. Listing the exact IDs on each model page would prevent this.

## Model behaviour we designed around

- **Long structured outputs could degenerate** into repeated blank lines until the token limit. Splitting work into smaller batches (four tool results per extraction call) fixed it.
- **Plausible but wrong hypotheses.** Ultra once proposed a root cause that sounded right but didn't explain the logged error. Giving the verifier the real logged errors, and a rule to refute any hypothesis that can't produce them, fixed that case.
- **Grounding by quote works.** Asking the model for a verbatim excerpt of each fact, then checking the excerpt against the tool output in code, made **100% of the citations in our evaluation valid**.

## Wish list

- Spend alerts or a hard spending limit per API key. We built our own per-investigation cost cap and daily budget to protect the free credits.
- A reranker model on Token Factory. We rerank with Nano instead.
- Network-off and timeout options for sandbox runs, stated clearly in the docs, so untrusted patches can be run safely.

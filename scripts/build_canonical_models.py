#!/usr/bin/env python3
"""Build karna/providers/canonical_models.json from live upstream sources.

Sources (in priority order; later ones fill gaps not covered by earlier):
  1. OpenRouter `/api/v1/models`       — ~343 models, rich capability data
  2. HuggingFace text-generation list  — ~1000 models, inferred capabilities
  3. Hand-curated `DIRECT_MODELS`       — Anthropic/OpenAI/Google direct-API
     entries that may not appear on OpenRouter with canonical slugs we
     actually use (e.g. `anthropic/claude-haiku-4.5` via the Anthropic SDK)

The output schema per alpha's directive:

    {"id": "<provider>/<model>",
     "provider": "<provider>",
     "context_window": int,
     "max_output": int,
     "supports_tools": bool,
     "supports_streaming": bool,
     "supports_vision": bool,
     "supports_thinking": bool,
     "cost_per_mtok_input": float,
     "cost_per_mtok_output": float,
     "source": "openrouter"|"hf"|"direct"}

Target: ≥1000 entries on dev. Rebuild with:

    python scripts/build_canonical_models.py \\
        --or-json /tmp/openrouter_models.json \\
        --hf-json /tmp/hf_models.json \\
        --out karna/providers/canonical_models.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# ─── Heuristics for HF-only entries ─────────────────────────────────────────

CONTEXT_BY_FAMILY = {
    # Each key is matched as a case-insensitive prefix of the HF model name.
    "qwen3": 262144,
    "qwen2.5": 131072,
    "qwen2": 32768,
    "qwen-": 32768,
    "llama-3.3": 131072,
    "llama-3.2": 131072,
    "llama-3.1": 131072,
    "llama-3": 8192,
    "llama-2": 4096,
    "mistral-7b": 32768,
    "mistral-nemo": 131072,
    "mistral-large": 131072,
    "mixtral": 32768,
    "gemma-3": 131072,
    "gemma-2": 8192,
    "gemma": 8192,
    "phi-4": 16384,
    "phi-3": 131072,
    "phi-2": 2048,
    "deepseek-v3": 65536,
    "deepseek-r1": 65536,
    "deepseek-coder": 16384,
    "codellama": 16384,
    "falcon": 8192,
    "yi-": 32768,
    "smol": 8192,
    "stablelm": 4096,
}

DEFAULT_CONTEXT = 4096

TOOL_CAPABLE_FAMILIES = {
    "qwen2.5",
    "qwen3",
    "llama-3.1",
    "llama-3.2",
    "llama-3.3",
    "mistral-large",
    "mistral-nemo",
    "mixtral",
    "gemma-3",
    "phi-3.5",
    "phi-4",
    "deepseek-v3",
    "deepseek-r1",
}

VISION_HINTS = (
    "vl",
    "vision",
    "multimodal",
    "image",
    "llava",
    "pixtral",
    "gemma-3",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "pix",
    "clip",
    "janus",
)

THINKING_HINTS = (
    "thinking",
    "o1",
    "o3",
    "o4",
    "deepseek-r1",
    "qwq",
    "r1-distill",
    "reasoning",
)


def _infer_family(name: str) -> str | None:
    lower = name.lower()
    # Longest prefix match to avoid "llama-3" swallowing "llama-3.3"
    best = None
    for fam in sorted(CONTEXT_BY_FAMILY, key=len, reverse=True):
        if fam in lower:
            best = fam
            break
    return best


def _infer_provider_from_id(model_id: str) -> str:
    """`org/model` → `org`. Normalize common aliases."""
    head = model_id.split("/", 1)[0].lower()
    return {
        "meta-llama": "meta",
        "mistralai": "mistral",
        "microsoft": "microsoft",
        "qwen": "qwen",
    }.get(head, head)


# ─── Normalizers ────────────────────────────────────────────────────────────


def _or_entry_to_canonical(raw: dict[str, Any]) -> dict[str, Any] | None:
    mid = raw.get("id")
    if not mid:
        return None
    arch = raw.get("architecture") or {}
    input_mod = arch.get("input_modalities") or []
    supported = set(raw.get("supported_parameters") or [])
    pricing = raw.get("pricing") or {}
    top = raw.get("top_provider") or {}

    def _price_per_mtok(field: str) -> float:
        v = pricing.get(field)
        try:
            # OpenRouter quotes per-token USD. Multiply by 1M for per-mtok.
            return round(float(v) * 1_000_000, 4) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    return {
        "id": mid,
        "provider": _infer_provider_from_id(mid),
        "context_window": int(raw.get("context_length") or top.get("context_length") or DEFAULT_CONTEXT),
        "max_output": int(top.get("max_completion_tokens") or 4096),
        "supports_tools": "tools" in supported,
        "supports_streaming": True,  # OpenRouter supports streaming for all its models
        "supports_vision": "image" in input_mod,
        "supports_thinking": "reasoning" in supported or "include_reasoning" in supported,
        "cost_per_mtok_input": _price_per_mtok("prompt"),
        "cost_per_mtok_output": _price_per_mtok("completion"),
        "source": "openrouter",
    }


def _hf_entry_to_canonical(raw: dict[str, Any]) -> dict[str, Any] | None:
    mid = raw.get("id") or raw.get("modelId")
    if not mid:
        return None
    if "/" not in mid:
        return None
    lower_id = mid.lower()
    tags = {t.lower() for t in (raw.get("tags") or [])}
    family = _infer_family(mid)
    ctx = CONTEXT_BY_FAMILY.get(family, DEFAULT_CONTEXT) if family else DEFAULT_CONTEXT
    tools = family in TOOL_CAPABLE_FAMILIES if family else False
    vision = any(h in lower_id for h in VISION_HINTS) or "image-text-to-text" in tags
    thinking = any(h in lower_id for h in THINKING_HINTS)

    return {
        "id": mid,
        "provider": _infer_provider_from_id(mid),
        "context_window": ctx,
        "max_output": min(ctx, 8192),
        "supports_tools": tools,
        "supports_streaming": True,
        "supports_vision": vision,
        "supports_thinking": thinking,
        "cost_per_mtok_input": 0.0,  # self-hosted / unknown
        "cost_per_mtok_output": 0.0,
        "source": "hf",
    }


# ─── Hand-curated direct-API entries ────────────────────────────────────────
# For Anthropic/OpenAI/Google direct-API flows, OpenRouter's slug differs from
# the canonical slug the SDK uses. Add these entries so `resolve_model` can
# map direct-API identifiers.

DIRECT_MODELS: list[dict[str, Any]] = [
    # Anthropic direct
    {
        "id": "anthropic/claude-opus-4-7",
        "provider": "anthropic",
        "context_window": 200000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 15.0,
        "cost_per_mtok_output": 75.0,
    },
    {
        "id": "anthropic/claude-opus-4-7-1m",
        "provider": "anthropic",
        "context_window": 1000000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 30.0,
        "cost_per_mtok_output": 150.0,
    },
    {
        "id": "anthropic/claude-sonnet-4-6",
        "provider": "anthropic",
        "context_window": 200000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 3.0,
        "cost_per_mtok_output": 15.0,
    },
    {
        "id": "anthropic/claude-haiku-4-5",
        "provider": "anthropic",
        "context_window": 200000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 1.0,
        "cost_per_mtok_output": 5.0,
    },
    {
        "id": "anthropic/claude-opus-4-5",
        "provider": "anthropic",
        "context_window": 200000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 15.0,
        "cost_per_mtok_output": 75.0,
    },
    {
        "id": "anthropic/claude-3-7-sonnet",
        "provider": "anthropic",
        "context_window": 200000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 3.0,
        "cost_per_mtok_output": 15.0,
    },
    # OpenAI direct
    {
        "id": "openai/gpt-5",
        "provider": "openai",
        "context_window": 400000,
        "max_output": 16384,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 5.0,
        "cost_per_mtok_output": 15.0,
    },
    {
        "id": "openai/gpt-5-mini",
        "provider": "openai",
        "context_window": 400000,
        "max_output": 16384,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 0.15,
        "cost_per_mtok_output": 0.6,
    },
    {
        "id": "openai/gpt-5-turbo",
        "provider": "openai",
        "context_window": 200000,
        "max_output": 16384,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 2.5,
        "cost_per_mtok_output": 7.5,
    },
    {
        "id": "openai/gpt-4o",
        "provider": "openai",
        "context_window": 128000,
        "max_output": 16384,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 2.5,
        "cost_per_mtok_output": 10.0,
    },
    {
        "id": "openai/gpt-4o-mini",
        "provider": "openai",
        "context_window": 128000,
        "max_output": 16384,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.15,
        "cost_per_mtok_output": 0.6,
    },
    {
        "id": "openai/o3",
        "provider": "openai",
        "context_window": 200000,
        "max_output": 100000,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 60.0,
        "cost_per_mtok_output": 240.0,
    },
    {
        "id": "openai/o3-mini",
        "provider": "openai",
        "context_window": 200000,
        "max_output": 100000,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": True,
        "cost_per_mtok_input": 1.1,
        "cost_per_mtok_output": 4.4,
    },
    {
        "id": "openai/o4",
        "provider": "openai",
        "context_window": 200000,
        "max_output": 100000,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 15.0,
        "cost_per_mtok_output": 60.0,
    },
    # Google direct
    {
        "id": "google/gemini-2.5-pro",
        "provider": "google",
        "context_window": 2000000,
        "max_output": 65536,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 2.5,
        "cost_per_mtok_output": 10.0,
    },
    {
        "id": "google/gemini-2.5-flash",
        "provider": "google",
        "context_window": 1000000,
        "max_output": 65536,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 0.3,
        "cost_per_mtok_output": 2.5,
    },
    {
        "id": "google/gemini-2.0-pro",
        "provider": "google",
        "context_window": 2000000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 1.25,
        "cost_per_mtok_output": 5.0,
    },
    {
        "id": "google/gemini-2.0-flash",
        "provider": "google",
        "context_window": 1000000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.1,
        "cost_per_mtok_output": 0.4,
    },
    # Local Ollama common library entries
    {
        "id": "ollama/llama3.3",
        "provider": "ollama",
        "context_window": 131072,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    {
        "id": "ollama/llama3.2",
        "provider": "ollama",
        "context_window": 131072,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    {
        "id": "ollama/llama3.2-vision",
        "provider": "ollama",
        "context_window": 131072,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    {
        "id": "ollama/qwen2.5-coder",
        "provider": "ollama",
        "context_window": 131072,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    {
        "id": "ollama/qwen3",
        "provider": "ollama",
        "context_window": 262144,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": True,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    {
        "id": "ollama/gemma3",
        "provider": "ollama",
        "context_window": 131072,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    {
        "id": "ollama/phi4",
        "provider": "ollama",
        "context_window": 16384,
        "max_output": 8192,
        "supports_tools": False,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    {
        "id": "ollama/mistral-nemo",
        "provider": "ollama",
        "context_window": 131072,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    {
        "id": "ollama/deepseek-r1",
        "provider": "ollama",
        "context_window": 65536,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": True,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
    },
    # xAI direct
    {
        "id": "xai/grok-4",
        "provider": "xai",
        "context_window": 256000,
        "max_output": 16384,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 5.0,
        "cost_per_mtok_output": 15.0,
    },
    {
        "id": "xai/grok-3",
        "provider": "xai",
        "context_window": 128000,
        "max_output": 16384,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": True,
        "cost_per_mtok_input": 3.0,
        "cost_per_mtok_output": 15.0,
    },
    # Cohere direct
    {
        "id": "cohere/command-a",
        "provider": "cohere",
        "context_window": 256000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "cost_per_mtok_input": 2.5,
        "cost_per_mtok_output": 10.0,
    },
    {
        "id": "cohere/command-r-plus",
        "provider": "cohere",
        "context_window": 128000,
        "max_output": 4096,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "cost_per_mtok_input": 2.5,
        "cost_per_mtok_output": 10.0,
    },
]
for m in DIRECT_MODELS:
    m["source"] = "direct"


# ─── Build ──────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--or-json", required=True, type=Path)
    ap.add_argument("--hf-json", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    entries: dict[str, dict[str, Any]] = {}

    # Direct first (canonical slugs we want stable)
    for e in DIRECT_MODELS:
        entries[e["id"]] = e

    # OpenRouter next
    or_raw = json.loads(args.or_json.read_text())
    for raw in or_raw.get("data", []):
        e = _or_entry_to_canonical(raw)
        if e and e["id"] not in entries:
            entries[e["id"]] = e

    # HuggingFace fill-in
    hf_raw = json.loads(args.hf_json.read_text())
    for raw in hf_raw:
        e = _hf_entry_to_canonical(raw)
        if e and e["id"] not in entries:
            entries[e["id"]] = e

    out_list = sorted(entries.values(), key=lambda m: m["id"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out_list, indent=2))

    # Tally
    by_source: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    for e in out_list:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1
        by_provider[e["provider"]] = by_provider.get(e["provider"], 0) + 1

    print(f"total={len(out_list)}")
    print("by_source:", dict(sorted(by_source.items(), key=lambda kv: -kv[1])))
    print("top_providers:", dict(sorted(by_provider.items(), key=lambda kv: -kv[1])[:12]))


if __name__ == "__main__":
    main()

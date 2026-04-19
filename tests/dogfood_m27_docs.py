#!/usr/bin/env python3
"""Dogfood test: Nellie uses M2.7 via OpenRouter to write its own documentation.

Tests the full pipeline:
1. Boot OpenRouter provider with minimax/minimax-m2.7
2. Read the karna/ codebase structure via tool-use
3. Generate README.md, ARCHITECTURE.md, API_REFERENCE.md
4. Verify output is coherent

Usage:
    export OPENROUTER_API_KEY=sk-or-v1-...
    cd /home/viraj/karna
    python tests/dogfood_m27_docs.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from karna.models import Message
from karna.providers.openrouter import OpenRouterProvider

REPO_ROOT = Path(__file__).parent.parent
MODEL = "minimax/minimax-m2.7"


def read_codebase_summary() -> str:
    """Read the codebase structure + key files to give M2.7 context."""

    # Directory tree
    tree_lines = []
    for p in sorted(REPO_ROOT.rglob("*.py")):
        rel = p.relative_to(REPO_ROOT)
        if "__pycache__" in str(rel) or ".egg-info" in str(rel):
            continue
        lines = len(p.read_text().splitlines())
        tree_lines.append(f"  {rel} ({lines} lines)")

    tree = "\n".join(tree_lines[:60])  # cap at 60 files

    # Key file contents (truncated)
    key_files = {}
    for name in [
        "karna/cli.py",
        "karna/config.py",
        "karna/models.py",
        "karna/providers/__init__.py",
        "karna/providers/openrouter.py",
        "karna/providers/base.py",
        "karna/tools/__init__.py",
        "karna/agents/__init__.py",
        "karna/security/guards.py",
        "karna/sessions/__init__.py",
        "karna/prompts/system.py",
        "pyproject.toml",
        "README.md",
    ]:
        path = REPO_ROOT / name
        if path.exists():
            content = path.read_text()
            # Truncate long files
            if len(content) > 2000:
                content = content[:2000] + "\n... (truncated)"
            key_files[name] = content

    files_section = ""
    for name, content in key_files.items():
        files_section += f"\n### {name}\n```\n{content}\n```\n"

    return f"""# Karna (Nellie) Codebase Summary

## File Tree ({len(tree_lines)} Python files)
{tree}

## Key File Contents
{files_section}
"""


async def generate_doc(provider: OpenRouterProvider, doc_type: str, context: str) -> str:
    """Call M2.7 to generate one documentation file."""

    prompts = {
        "README": """Write a comprehensive README.md for the Karna project (CLI binary: nellie).
Include: project description, features, installation, quickstart, configuration,
available commands, provider support, tool list, security model, and contributing guide.
Make it professional and welcoming. Use the codebase context below.""",
        "ARCHITECTURE": """Write an ARCHITECTURE.md for the Karna project.
Include: system overview, component diagram (text-based), provider abstraction,
tool system, agent loop, session management, security model, configuration flow,
and extension points. Be technical but accessible.""",
        "API_REFERENCE": """Write an API_REFERENCE.md for the Karna project.
Document: all CLI commands (nellie <cmd>), all providers and their config,
all tools and their parameters, configuration file format, environment variables,
and the Python API for embedding. Be precise with types and defaults.""",
    }

    prompt = prompts[doc_type]

    messages = [
        Message(role="user", content=f"{prompt}\n\n{context}"),
    ]

    print(f"  Calling M2.7 for {doc_type}...", flush=True)
    t0 = time.time()

    try:
        response: Message = await provider.complete(
            messages=messages,
            system_prompt=(
                "You are a technical documentation writer. Write clear, accurate,"
                " comprehensive documentation based on the source code provided."
                " Output raw markdown only \u2014 no commentary."
            ),
            max_tokens=4000,
            temperature=0.3,
        )
        elapsed = time.time() - t0

        content = response.content or ""

        # Get usage from provider's tracked stats
        usage = provider.cumulative_usage
        print(f"  ✓ {doc_type}: {len(content)} chars, {elapsed:.1f}s, usage: {usage}", flush=True)
        return content
    except Exception as e:
        import traceback

        print(f"  ✗ {doc_type}: {e}", flush=True)
        traceback.print_exc()
        return f"# Error generating {doc_type}\n\n{e}"


async def main():
    print("=" * 60)
    print("  Nellie Dogfood Test: M2.7 writes its own docs")
    print("=" * 60)

    # Check API key
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: Set OPENROUTER_API_KEY", file=sys.stderr)
        return 1

    # Read codebase
    print("\n[1] Reading codebase...", flush=True)
    context = read_codebase_summary()
    print(f"  Context: {len(context)} chars ({len(context.split())} words)")

    # Init provider
    print(f"\n[2] Initializing OpenRouter provider (model: {MODEL})...", flush=True)
    provider = OpenRouterProvider(model=MODEL)

    # Generate 3 docs
    print("\n[3] Generating documentation via M2.7...", flush=True)
    t_start = time.time()

    docs = {}
    for doc_type in ["README", "ARCHITECTURE", "API_REFERENCE"]:
        docs[doc_type] = await generate_doc(provider, doc_type, context)

    total_time = time.time() - t_start
    total_chars = sum(len(d) for d in docs.values())

    # Write output
    print("\n[4] Writing generated docs...", flush=True)
    out_dir = REPO_ROOT / "docs" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)

    for doc_type, content in docs.items():
        out_path = out_dir / f"{doc_type}.md"
        out_path.write_text(content)
        print(f"  {out_path.relative_to(REPO_ROOT)}: {len(content)} chars")

    # Validation
    print("\n[5] Validation...", flush=True)
    all_pass = True
    for doc_type, content in docs.items():
        checks = {
            "non-empty": len(content) > 100,
            "has headings": "#" in content,
            "mentions karna": "karna" in content.lower() or "nellie" in content.lower(),
            "no error": "Error generating" not in content,
            ">500 chars": len(content) > 500,
        }
        status = "✓" if all(checks.values()) else "✗"
        failed = [k for k, v in checks.items() if not v]
        print(f"  {status} {doc_type}: {', '.join(failed) if failed else 'all checks pass'}")
        if not all(checks.values()):
            all_pass = False

    print(f"\n{'=' * 60}")
    print(f"  Total: {total_chars} chars generated in {total_time:.1f}s")
    print(f"  Model: {MODEL}")
    print(f"  Verdict: {'PASS ✓' if all_pass else 'FAIL ✗'}")
    print("  Output: docs/generated/")
    print(f"{'=' * 60}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

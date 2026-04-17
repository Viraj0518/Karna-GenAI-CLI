# Copilot Agent Instructions — Nellie (Karna-GenAI-CLI)

## About this repo

Nellie is Karna's internal AI agent harness. Python 3.10+, CLI binary `nellie`, package name `karna`.

## When assigned an issue

1. Read `docs/DEVELOPER_GUIDE.md` and `docs/CODEBASE_MAP.md` first — they explain every module
2. Read `KARNA.md` if it exists in the repo root
3. Understand the architecture: `CLI → REPL → Agent Loop → Provider → Tools`
4. Check existing tests in `tests/` that cover the area you're modifying

## Code style

- Python 3.10+ (use `X | Y` union syntax, not `Optional[X]`)
- `ruff` for linting (line-length 120)
- Type hints on all public functions
- Module-level docstring on every `.py` file
- Tests in `tests/` mirroring `karna/` structure
- Async-first (tools + providers are async)

## Key patterns

- **Tools** inherit from `BaseTool` in `karna/tools/base.py` — implement `execute()`, set `name`, `description`, `parameters` (JSON Schema), and `sequential=True` if the tool mutates state
- **Providers** inherit from `BaseProvider` in `karna/providers/base.py` — implement `complete()` and `stream()`
- **Hooks** use the dispatcher in `karna/hooks/dispatcher.py` — register via `HookType` enum
- **Skills** are `.md` files parsed by `karna/skills/loader.py`

## Testing

```bash
pytest tests/ -q  # must pass 415+ tests
ruff check karna/
```

## Don't

- Don't add telemetry or analytics
- Don't hardcode API keys
- Don't break the `sequential` tool safety (bash/write/edit must never run in parallel)
- Don't modify `karna/security/guards.py` without understanding all 4 guard types
- Don't add external dependencies without checking `pyproject.toml` optional-deps pattern

## Branch strategy

- Create a feature branch from `main`
- PR title: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`
- Include test changes with code changes
- Ensure `pytest tests/ -q` passes before opening PR

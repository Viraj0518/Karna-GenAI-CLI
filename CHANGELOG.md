# Changelog

All notable changes to Nellie will be documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-04-17

### Added

- Initial release — Phase 1-4 scaffold
- Providers: OpenRouter, OpenAI, Anthropic, Azure, Local, Vertex AI, AWS Bedrock
- Multi-credential failover across same-provider instances
- Tools: bash, read, write, edit, grep, glob, web_fetch, web_search, clipboard, image, git_ops, mcp, task, monitor
- TUI via Rich with streaming tool output, slash commands, multiline editing
- Skills system (agentskills.io compatible)
- Auto-memory with typed entries
- Lifecycle hooks (pre/post tool use, bash-error, user-prompt-submit)
- Auto-compaction on context overflow
- SQLite session persistence with FTS5 search
- Cost tracking
- Permission gate (ALLOW/ASK/DENY + remember)
- Security: path traversal guard, SSRF DNS-pin, secret scrubber, safe_mode bash default

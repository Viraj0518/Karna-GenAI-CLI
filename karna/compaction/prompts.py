"""Summarization prompt templates for conversation compaction.

Adapted from upstream compact/prompt.ts with simplified structure
for Karna's interactive agent use case.

See NOTICES.md for attribution.
"""

SUMMARY_PROMPT = """Summarize the following conversation for context continuity.

Focus on:
1. What the user asked for and key decisions made
2. What code/files were changed and why
3. What tools were used and their results
4. Any open questions or remaining tasks
5. Important technical context (versions, paths, configs)

Be concise but preserve anything needed to continue the conversation intelligently.

<conversation>
{messages}
</conversation>

Produce a structured summary in under 500 tokens."""

COMPACT_SYSTEM_PROMPT = """\
You are a conversation summarizer. Your job is to produce a concise, \
structured summary that preserves all context needed to continue the \
conversation. Respond ONLY with the summary text -- no tool calls, \
no questions, no preamble."""

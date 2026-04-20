"""Document chunker — split files into overlapping chunks for embedding.

Design:
- Splits on paragraph / section boundaries (never mid-sentence).
- Each chunk is ~512 tokens with 128-token overlap between adjacent chunks.
- Preserves file path + line range metadata per chunk.

Token estimation uses ``karna.tokens.count_tokens`` which delegates to
tiktoken when available and falls back to len//4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from karna.tokens import count_tokens

# Target chunk size and overlap in tokens.
DEFAULT_CHUNK_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 128


@dataclass(frozen=True)
class Chunk:
    """A single chunk of a document with source metadata."""

    text: str
    source_path: str
    start_line: int
    end_line: int
    chunk_index: int
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def source_label(self) -> str:
        """Human-readable source attribution."""
        return f"{self.source_path}:{self.start_line}-{self.end_line}"


# Paragraph boundary pattern — two or more newlines, or a markdown heading.
_PARAGRAPH_RE = re.compile(r"\n{2,}|(?=^#{1,6}\s)", re.MULTILINE)

# Sentence boundary — period/question/exclamation followed by whitespace.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraph-level blocks."""
    blocks = _PARAGRAPH_RE.split(text)
    return [b.strip() for b in blocks if b.strip()]


def _line_number_at_offset(text: str, offset: int) -> int:
    """Return the 1-based line number for a character offset."""
    return text[:offset].count("\n") + 1


def chunk_text(
    text: str,
    source_path: str = "<unknown>",
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split *text* into overlapping chunks.

    Strategy:
    1. Split into paragraphs (respecting markdown headings).
    2. Accumulate paragraphs into chunks up to *chunk_tokens*.
    3. When a chunk is full, start the next chunk with *overlap_tokens*
       worth of trailing text from the previous chunk.
    4. Never split mid-sentence — if a single paragraph exceeds the
       chunk size, split on sentence boundaries instead.

    Returns a list of ``Chunk`` objects with line-range metadata.
    """
    if not text.strip():
        return []

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    # Expand any paragraph that exceeds the chunk size into sentences.
    segments: list[str] = []
    for para in paragraphs:
        if count_tokens(para) <= chunk_tokens:
            segments.append(para)
        else:
            # Split by sentence boundaries
            sentences = _SENTENCE_RE.split(para)
            segments.extend(s.strip() for s in sentences if s.strip())

    chunks: list[Chunk] = []
    current_segments: list[str] = []
    current_tokens = 0
    # Forward cursor — ensures each chunk's position search starts past
    # the previous chunk's start, avoiding false matches when identical
    # text appears earlier in the document.
    _search_cursor = 0

    def _flush() -> None:
        """Emit the accumulated segments as a chunk."""
        nonlocal _search_cursor
        if not current_segments:
            return
        chunk_text_str = "\n\n".join(current_segments)

        # Compute line range by finding the chunk text in the original,
        # searching forward from the cursor to avoid matching earlier
        # occurrences of the same text.
        start_offset = text.find(current_segments[0], _search_cursor)
        if start_offset < 0:
            start_offset = _search_cursor
        end_segment = current_segments[-1]
        end_offset = text.find(end_segment, start_offset)
        if end_offset < 0:
            end_offset = start_offset
        end_offset += len(end_segment)

        # Advance cursor past this chunk's start so the next chunk
        # searches further into the document.
        _search_cursor = start_offset + 1

        start_line = _line_number_at_offset(text, start_offset)
        end_line = _line_number_at_offset(text, end_offset)

        chunks.append(
            Chunk(
                text=chunk_text_str,
                source_path=source_path,
                start_line=start_line,
                end_line=end_line,
                chunk_index=len(chunks),
            )
        )

    for seg in segments:
        seg_tokens = count_tokens(seg)

        if current_tokens + seg_tokens > chunk_tokens and current_segments:
            _flush()

            # Build overlap from the tail of the previous chunk.
            overlap_segs: list[str] = []
            overlap_tok = 0
            for prev_seg in reversed(current_segments):
                prev_tok = count_tokens(prev_seg)
                if overlap_tok + prev_tok > overlap_tokens:
                    break
                overlap_segs.insert(0, prev_seg)
                overlap_tok += prev_tok

            current_segments = overlap_segs
            current_tokens = overlap_tok

        current_segments.append(seg)
        current_tokens += seg_tokens

    _flush()
    return chunks


def chunk_file(
    path: Path,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Read a file and return its chunks.

    Skips binary files and files that cannot be decoded as UTF-8.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []

    if not text.strip():
        return []

    # Skip likely-binary files (high ratio of null bytes or control chars).
    control_chars = sum(1 for c in text[:4096] if ord(c) < 32 and c not in "\n\r\t")
    if control_chars > len(text[:4096]) * 0.1:
        return []

    return chunk_text(
        text,
        source_path=str(path),
        chunk_tokens=chunk_tokens,
        overlap_tokens=overlap_tokens,
    )

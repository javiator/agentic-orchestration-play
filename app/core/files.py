"""Uploaded-file parsing: txt / md / csv are read as text, pdf via pypdf.

Parsed content is injected verbatim into the shared user message, so every
research agent and the judge see exactly the same document text.
"""

import io

from pypdf import PdfReader

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv"}
MAX_CHARS_PER_FILE = 60_000  # keep any single document from blowing the context


def parse_upload(filename: str, data: bytes) -> dict[str, str]:
    """Return {"name", "content", "chars"}; raises ValueError on unsupported type."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        content = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif any(lower.endswith(ext) for ext in TEXT_EXTENSIONS):
        content = data.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type: {filename} (allowed: txt, md, csv, pdf)")

    content = content.strip()
    if len(content) > MAX_CHARS_PER_FILE:
        content = content[:MAX_CHARS_PER_FILE] + "\n[... truncated ...]"
    return {"name": filename, "content": content, "chars": str(len(content))}

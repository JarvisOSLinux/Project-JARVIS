"""Print the text of a PDF the user attached, as JSON (runtime/attachments.py).

The daemon runs this file in a process of its own, with a time limit, so a
hostile PDF costs at most that process's limit: pypdf bounds one page's work,
not a whole document's. Run by path with ``python -I``, so this folder (whose
``io.py`` would shadow the standard library's) is not on ``sys.path``.

Usage: ``python -I pdf_text.py <path> <max chars> <max pages>``; prints
``{"text", "pages", "read"}``.
"""

import json
import sys

from pypdf import PdfReader


def pdf_text(path: str, max_chars: int, max_pages: int) -> dict:
    """The text of the first pages, read no further than `max_chars` needs."""
    reader = PdfReader(path)
    if reader.is_encrypted:
        reader.decrypt("")
    parts, total, read = [], 0, 0
    for page in reader.pages[:max_pages]:
        text = page.extract_text() or ""
        parts.append(text)
        total += len(text)
        read += 1
        if total > max_chars:
            break
    return {"text": "\n".join(parts).strip(), "pages": len(reader.pages), "read": read}


if __name__ == "__main__":
    json.dump(pdf_text(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])), sys.stdout)

"""Environment smoke test.

Run after `pip install -r requirements.txt` to confirm every dependency the
pipeline needs actually imports on this machine. Reported in the README so a
reviewer can verify setup in one command.
"""
from __future__ import annotations

import importlib
import sys

MODULES = [
    ("trafilatura", "HTML main-content extraction"),
    ("bs4", "targeted DOM extraction (FAQ, tables)"),
    ("lxml", "HTML parser backend"),
    ("fitz", "PyMuPDF - PDF parsing"),
    ("fastembed", "ONNX embeddings (no torch)"),
    ("groq", "Groq client - ASR + low-latency LLM"),
    ("google.genai", "Gemini client - agent reasoning"),
    ("edge_tts", "neural TTS incl. fil-PH / id-ID voices"),
    ("fastapi", "retrieval + voice + nudge APIs"),
    ("uvicorn", "ASGI server"),
    ("websockets", "streaming transport"),
    ("rank_bm25", "lexical retrieval"),
    ("datasketch", "MinHash near-duplicate detection"),
    ("numpy", "vector math"),
    ("webrtcvad", "voice activity detection"),
    ("soundfile", "audio IO"),
    ("httpx", "HTTP client"),
    ("pydantic_settings", "config"),
    ("rich", "console output"),
    ("tenacity", "retries"),
]


def main() -> int:
    ok: list[str] = []
    bad: list[tuple[str, str]] = []

    for name, purpose in MODULES:
        try:
            importlib.import_module(name)
            ok.append(name)
        except Exception as exc:  # noqa: BLE001
            bad.append((name, f"{type(exc).__name__}: {exc}"[:90]))

    print(f"Python {sys.version.split()[0]}")
    print(f"OK   {len(ok)}/{len(MODULES)}")
    for name in ok:
        print(f"  [+] {name}")

    if bad:
        print(f"\nFAILED {len(bad)}:")
        for name, err in bad:
            purpose = dict((m, p) for m, p in MODULES)[name]
            print(f"  [-] {name:<20} {purpose}")
            print(f"      {err}")
        return 1

    print("\nAll dependencies import cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

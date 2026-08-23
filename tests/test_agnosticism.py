"""Agnosticism test — no niche terms in src/."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED = [
    "coloring",
    "coloring pages",
    "battlemap",
    "battle map",
    "fantasy",
    "mandala",
    "mandalas",
    "witchy",
    "wizard",
    "kindle",
    "amazon",
    "ttrpg",
    "printable game",
    # Character/niche decisions that must live in the pack, never in the engine
    "blaze",
    "pip",
    "pebble",
    "clover",
    "dragon",
    "dragons",
    "torch",
    "antorcha",
    "caracol",
    "kawaii",
    "chibi",
    "unicorn",
    "mermaid",
    "princess",
]


def test_no_niche_terms_in_src():
    """Reject generic product/niche terms that should live in the pack.

    Technical terms (KDP abbreviation inside the audit rationale, model names,
    public APIs) are allowed — they are not niche signals.
    """
    src = ROOT / "src"
    for f in src.rglob("*"):
        if f.is_file() and f.suffix in {".py", ".md", ".yaml", ".yml"}:
            text = f.read_text(errors="ignore")
            for term in BANNED:
                pattern = r"\b" + re.escape(term) + r"\b"
                if re.search(pattern, text, re.IGNORECASE):
                    raise AssertionError(f"{f}: found niche term {term!r}")


def test_no_niche_terms_in_root_yaml():
    """runtime/default.yaml may mention general things like 'marketplace' but not specific ones."""
    runtime = ROOT / "runtime" / "default.yaml"
    if not runtime.exists():
        return
    text = runtime.read_text()
    for term in BANNED:
        assert term.lower() not in text.lower(), f"runtime/default.yaml: found {term!r}"

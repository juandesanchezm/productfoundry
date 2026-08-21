"""Agnosticism test — no niche terms in src/."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED = [
    "coloring",
    "coloring pages",
    "battlemap",
    "battle map",
    "dnd",
    "d&d",
    "fantasy",
    "mandala",
    "mandalas",
    "witchy",
    "wizard",
    "dragon",
    "dragon's",
    "etsy",
    "gumroad",
    "kdp",
    "kindle",
    "amazon",
    "ttrpg",
    "printable game",
]


def test_no_niche_terms_in_src():
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

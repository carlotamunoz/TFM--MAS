import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
PROCESSED_DIR = BASE_DIR / "data" / "processed"

IN_PAGES = PROCESSED_DIR / "pages.jsonl"
OUT_LEX = PROCESSED_DIR / "lexicon.jsonl"
OUT_LEX.parent.mkdir(parents=True, exist_ok=True)

# Palabras típicas de inicio en estos AJP
LEX_MARKERS = re.compile(
    r"\b(LEXICON|ACRONYMS|TERMS AND DEFINITIONS|TERMS & DEFINITIONS|LEX-\s*\d+)\b",
    re.IGNORECASE
)

def looks_like_lexicon_page(text: str) -> bool:
    if not text:
        return False
    if LEX_MARKERS.search(text):
        return True
    # A veces no pone "LEXICON" pero sí listados estilo "AAA  something"
    # Heurística: muchas líneas con acrónimos (2-10 mayúsculas) seguidas de espacios
    acronymish = len(re.findall(r"\b[A-Z]{2,10}\b\s{1,5}[A-Za-z].{0,60}", text))
    return acronymish >= 8

with IN_PAGES.open("r", encoding="utf-8") as fin, OUT_LEX.open("w", encoding="utf-8") as fout:
    for line in fin:
        page = json.loads(line)
        if looks_like_lexicon_page(page["text"]):
            rec = {
                "doc_id": page["doc_id"],
                "page_index": page["page_index"],
                "text": page["text"],
            }
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(f"OK -> {OUT_LEX}")

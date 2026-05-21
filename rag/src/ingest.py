import json
from pathlib import Path

try:
    from pypdf import PdfReader
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Missing dependency 'pypdf'. Install it with: python -m pip install pypdf"
    ) from exc

try:
    from tqdm import tqdm
except ModuleNotFoundError:
    # Fallback when tqdm is not installed: iterate without progress bar.
    def tqdm(iterable, **kwargs):
        return iterable

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

PDFS = [
    ("AJP-3.1", RAW_DIR / "AJP_3_1_Maritime_Ops_EdB.pdf"),
    ("AJP-3.2", RAW_DIR / "AJP-3.2_EDB_V1_E_2288.pdf"),
    ("AJP-3.3", RAW_DIR / "AJP_3_3_EdC_V1.pdf"),
]

OUT_PAGES = PROCESSED_DIR / "pages.jsonl"
OUT_PAGES.parent.mkdir(parents=True, exist_ok=True)

def clean(text: str) -> str:
    return " ".join((text or "").replace("\u00ad", "").split())

with OUT_PAGES.open("w", encoding="utf-8") as f:
    for doc_id, pdf_path in PDFS:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        reader = PdfReader(pdf_path)
        for i, page in enumerate(tqdm(reader.pages, desc=f"Reading {doc_id}")):
            txt = clean(page.extract_text())
            if not txt:
                continue
            rec = {
                "doc_id": doc_id,
                "page_index": i,        # 0-based
                "text": txt,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(f"OK -> {OUT_PAGES}")

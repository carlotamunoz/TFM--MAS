import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
PROCESSED_DIR = BASE_DIR / "data" / "processed"

IN_PAGES = PROCESSED_DIR / "pages.jsonl"
OUT_CHUNKS = PROCESSED_DIR / "chunks.jsonl"
OUT_CHUNKS.parent.mkdir(parents=True, exist_ok=True)

# Chunking simple por longitud aproximada (caracteres).
# Luego lo refinamos por headings/secciones si hace falta.
CHUNK_SIZE = 2200
OVERLAP = 250

def chunk_text(text: str, chunk_size: int, overlap: int):
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        chunks.append(text[start:end])
        if end == n:
            break
        start = max(0, end - overlap)
    return chunks

with IN_PAGES.open("r", encoding="utf-8") as fin, OUT_CHUNKS.open("w", encoding="utf-8") as fout:
    for line in fin:
        page = json.loads(line)
        for j, ch in enumerate(chunk_text(page["text"], CHUNK_SIZE, OVERLAP)):
            rec = {
                "chunk_id": f'{page["doc_id"]}_p{page["page_index"]}_c{j}',
                "doc_id": page["doc_id"],
                "page_index": page["page_index"],
                "text": ch,
            }
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(f"OK -> {OUT_CHUNKS}")

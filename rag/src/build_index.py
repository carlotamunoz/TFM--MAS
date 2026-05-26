#!/usr/bin/env python
"""
rag/src/build_index.py
======================
Re-ingests los tres PDFs de doctrina AJP en ChromaDB con chunking
por sección (Chapter > Section) en lugar de por página/caracteres.

Estrategia:
  1. Leer cada PDF página a página con pypdf.
  2. Limpiar cabeceras repetidas (AJP-3.X, Edition...) y blancos.
  3. Detectar headings (Chapter / Section / Annex) dentro de cada página.
  4. Acumular texto bajo la sección activa.
  5. Al cambiar de sección, guardar el chunk acumulado.
  6. Si un chunk supera MAX_CHUNK_CHARS, dividir con solapamiento
     (prefijando el heading al inicio de cada parte).
  7. Embebido con all-MiniLM-L6-v2 e indexado en ChromaDB.

Uso (desde la raíz del proyecto):
    .venv/Scripts/python rag/src/build_index.py
"""

import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Dependencias ──────────────────────────────────────────────────────────────
try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("Instala PyMuPDF:  pip install pymupdf")

try:
    import chromadb
    from sentence_transformers import SentenceTransformer
except ImportError:
    sys.exit("Instala: pip install chromadb sentence-transformers")

# ── Rutas ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parents[1]   # rag/
RAW_DIR    = BASE_DIR / "data" / "raw"
CHROMA_DIR = BASE_DIR / "data" / "chroma"

COLLECTION_NAME  = "ajp_doctrine_chunks"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"

# ── Parámetros de chunking ────────────────────────────────────────────────────
MAX_CHUNK_CHARS = 1600   # ~220 tokens → cómodo para MiniLM-L6-v2 (max 256 tok)
OVERLAP_CHARS   = 200    # solapamiento entre sub-chunks de la misma sección
MIN_CHUNK_CHARS = 300    # descartar chunks demasiado cortos

BATCH_SIZE = 32          # tamaño de lote para ChromaDB

# ── Corpus ────────────────────────────────────────────────────────────────────
PDFS: List[Tuple[str, str, Path]] = [
    ("AJP-3.1", "maritime", RAW_DIR / "AJP-3.1.pdf"),
    ("AJP-3.2", "land",     RAW_DIR / "AJP-3.2.pdf"),
    ("AJP-3.3", "air",      RAW_DIR / "AJP-3.3.pdf"),
]

# ── Regex ─────────────────────────────────────────────────────────────────────

# Cabecera de página: "AJP-3.3" / "AJP 3.1" / "Edition C Version 1" / "Intentionally blank"
_PAGE_NOISE = re.compile(
    r"\bAJP[-\s]?\d+(?:\.\d+)?\b"
    r"|\bEdition\s+\S+(?:,?\s+[Vv]ersion\s+\S+)?\b"
    r"|\bIntentionally\s+blank\b",
    re.IGNORECASE,
)

# Headings reales en el documento.
# Acepta guión simple (-), en-dash (–, U+2013), em-dash (—, U+2014),
# y otros separadores que PyMuPDF puede emitir al extraer PDFs.
_DASH = r"[\-\u2013\u2014\u2212\u2010\u2011]"

_CHAPTER_RE = re.compile(
    rf"Chapter\s+\d+\s*{_DASH}\s*\w.{{4,90}}",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(
    rf"Section\s+\d+\s*{_DASH}\s*\w.{{4,90}}",
    re.IGNORECASE,
)
_ANNEX_RE = re.compile(
    rf"Annex\s+[A-Z](?:\s*{_DASH}\s*\w.{{0,90}})?",
    re.IGNORECASE,
)

# Entrada de TOC: el heading va seguido de su número de página al final de línea.
# Los AJP usan tanto números simples ("47") como referencias de anexo ("A-1", "B-3").
_TOC_ENTRY = re.compile(
    r"(?:Chapter|Section|Annex)\s+\S+.{3,90}\s{1,5}(?:\d+|[A-Z]-\d+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


# ── Helpers de limpieza ───────────────────────────────────────────────────────

def _clean_page(raw: str) -> str:
    """Elimina cabeceras repetidas y normaliza espacios manteniendo saltos de línea."""
    text = _PAGE_NOISE.sub("", raw)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _is_blank(text: str) -> bool:
    """Página sin contenido útil (< MIN_CHUNK_CHARS o "Intentionally blank")."""
    t = text.strip()
    if len(t) < MIN_CHUNK_CHARS:
        return True
    # Páginas que son solo números romanos / árabigos (números de página sueltos)
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    if lines and all(re.fullmatch(r"[\divxlcdmIVXLCDM]+", l) for l in lines):
        return True
    return False


def _is_toc_page(text: str) -> bool:
    """True si la página es una tabla de contenidos (≥3 entradas estilo 'Heading N')."""
    matches = _TOC_ENTRY.findall(text)
    return len(matches) >= 3


# ── Detección de headings ─────────────────────────────────────────────────────

def _find_page_heading(text: str) -> Optional[Tuple[str, str]]:
    """
    Busca el PRIMER heading significativo en las primeras 400 chars de la página.
    Solo detecta headings que aparecen al inicio (no entradas de TOC a mitad de página).

    Devuelve (level, heading_text) o None.
    level: 'chapter' | 'section' | 'annex'
    """
    sample = text[:400]

    for level, rx in [
        ("chapter", _CHAPTER_RE),
        ("section", _SECTION_RE),
        ("annex",   _ANNEX_RE),
    ]:
        m = rx.search(sample)
        if not m:
            continue
        raw_heading = m.group(0).strip()
        # Descartar si es entrada de TOC (termina en número de página o ref A-N)
        if re.search(r"\s+(?:\d+|[A-Z]-\d+)\s*$", raw_heading):
            continue
        heading = " ".join(raw_heading.split())
        return level, heading

    return None


# ── División de chunks largos ─────────────────────────────────────────────────

def _split_into_chunks(text: str, heading_prefix: str = "") -> List[str]:
    """
    Divide `text` en sub-chunks de ≤ MAX_CHUNK_CHARS con solapamiento.
    Los sub-chunks de continuación llevan el heading como prefijo para
    que el embedding tenga contexto de sección.

    Garantiza avance mínimo de 1 carácter por iteración para evitar loops infinitos.
    """
    if not text:
        return []
    # Protección: si el texto es demasiado grande (> 200 KB), lo truncamos
    if len(text) > 200_000:
        text = text[:200_000]
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]

    chunks: List[str] = []
    start = 0
    n = len(text)
    MIN_ADVANCE = max(1, MAX_CHUNK_CHARS - OVERLAP_CHARS)  # avance mínimo garantizado

    while start < n:
        end = min(n, start + MAX_CHUNK_CHARS)
        if end < n:
            # Buscar límite de oración en la segunda mitad del chunk
            mid = start + MAX_CHUNK_CHARS // 2
            bp = text.rfind(". ", mid, end)
            if bp != -1:
                end = bp + 2

        # Garantizar que end avanza al menos MIN_ADVANCE sobre start
        end = max(end, start + MIN_ADVANCE)
        end = min(end, n)

        chunk = text[start:end].strip()
        if len(chunk) >= MIN_CHUNK_CHARS:
            prefix = (heading_prefix + "\n") if (heading_prefix and start > 0) else ""
            chunks.append(prefix + chunk)

        # Avanzar; nunca retroceder
        next_start = end - OVERLAP_CHARS
        start = max(next_start, start + 1)  # garantiza avance mínimo

    return chunks


# ── Inferir categoría ─────────────────────────────────────────────────────────

def _infer_category(text: str) -> str:
    t = text.lower()
    if "lexicon" in t or "terms and definitions" in t or "acronyms" in t:
        return "glossary"
    if re.search(r"\bphase\s+\d\b|\bstep\s+\d\b|\bprocedure\b", t):
        return "procedure"
    return "doctrine"


# ── Extracción por sección ────────────────────────────────────────────────────

def extract_sections(
    pdf_path: Path, doc_id: str, domain: str
) -> List[Dict[str, Any]]:
    """
    Lee un PDF y devuelve lista de chunks con metadata de sección.
    """
    doc = fitz.open(str(pdf_path))

    # Estado de la máquina de secciones
    current_chapter  = ""
    current_section  = ""
    accumulated_pages: List[Tuple[int, str]] = []  # [(page_num, text)]
    all_chunks: List[Dict[str, Any]] = []

    def _finalize_section() -> None:
        """Guarda el texto acumulado como uno o varios chunks."""
        if not accumulated_pages:
            return

        full_text = "\n".join(t for _, t in accumulated_pages).strip()
        if len(full_text) < MIN_CHUNK_CHARS:
            return

        # section_path
        if current_chapter and current_section:
            section_path = f"{current_chapter} > {current_section}"
        elif current_chapter:
            section_path = current_chapter
        else:
            section_path = current_section or "(preamble)"

        heading_prefix = current_section or current_chapter

        sub_chunks = _split_into_chunks(full_text, heading_prefix)
        page_start = accumulated_pages[0][0]
        page_end   = accumulated_pages[-1][0]

        for i, chunk_text in enumerate(sub_chunks):
            all_chunks.append({
                "text":         chunk_text,
                "source_doc":   doc_id,
                "domain":       domain,
                "chapter":      current_chapter,
                "section":      current_section,
                "section_path": section_path,
                "page_start":   page_start,
                "page_end":     page_end,
                "part":         i + 1,
                "category":     _infer_category(chunk_text),
            })

    for page_idx in range(len(doc)):
        page_num = page_idx + 1
        page_obj = doc[page_idx]
        raw_text = page_obj.get_text()  # PyMuPDF
        text     = _clean_page(raw_text)

        if _is_blank(text) or _is_toc_page(text):
            continue

        # Buscamos heading SOLO en las primeras 400 chars (inicio de página).
        # Así evitamos confundir con headings internos o mid-page que son ruido.
        heading_info = _find_page_heading(text)

        if heading_info:
            level, heading = heading_info

            # Finalizar sección anterior si tenía contenido
            _finalize_section()
            accumulated_pages = []

            # Actualizar estado
            if level == "chapter":
                current_chapter = heading
                current_section = ""
            elif level == "section":
                current_section = heading
            elif level == "annex":
                current_chapter = heading
                current_section = ""

        # La página completa se acumula bajo la sección activa
        accumulated_pages.append((page_num, text))

    # Finalizar la última sección
    _finalize_section()

    return all_chunks


# ── ChromaDB ──────────────────────────────────────────────────────────────────

def rebuild_index() -> None:
    print(f"\n{'='*60}")
    print(f"  Rebuilding ChromaDB: {COLLECTION_NAME}")
    print(f"  Embed model : {EMBED_MODEL_NAME}")
    print(f"  Max chunk   : {MAX_CHUNK_CHARS} chars")
    print(f"{'='*60}\n")

    print("Cargando modelo de embeddings...")
    model = SentenceTransformer(EMBED_MODEL_NAME)
    print("  Modelo listo.\n")

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Borrar colección existente y recrear
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"  Colección '{COLLECTION_NAME}' eliminada.")
    except Exception:
        pass

    collection = client.create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    print(f"  Colección '{COLLECTION_NAME}' creada.\n")

    # Extraer chunks de cada PDF
    all_chunks: List[Dict[str, Any]] = []

    for doc_id, domain, pdf_path in PDFS:
        if not pdf_path.exists():
            print(f"  [SKIP] {pdf_path.name} no encontrado.")
            continue

        print(f"Procesando {doc_id} ({domain})...")
        chunks = extract_sections(pdf_path, doc_id, domain)
        print(f"  -> {len(chunks)} chunks extraidos")

        # Resumen de secciones detectadas
        from collections import Counter
        sec_counts = Counter(
            c["section"] or c["chapter"] or "(sin sección)" for c in chunks
        )
        for sec, cnt in sec_counts.most_common(8):
            label = sec[:70] if sec else "(sin sección)"
            print(f"    {label:<70s} {cnt:3d}")
        print()

        all_chunks.extend(chunks)

    print(f"Total chunks: {len(all_chunks)}\n")
    print("Embebiendo e indexando...\n")

    # Insertar en ChromaDB por lotes
    total = len(all_chunks)
    for i in range(0, total, BATCH_SIZE):
        batch     = all_chunks[i : i + BATCH_SIZE]
        texts     = [c["text"] for c in batch]
        embeddings = model.encode(texts, show_progress_bar=False).tolist()

        collection.add(
            ids       =[str(uuid.uuid4()) for _ in batch],
            embeddings=embeddings,
            documents =texts,
            metadatas =[
                {
                    "source_doc":   c["source_doc"],
                    "domain":       c["domain"],
                    "chapter":      c["chapter"],
                    "section":      c["section"],
                    "section_path": c["section_path"],
                    "page_start":   c["page_start"],
                    "page_end":     c["page_end"],
                    "category":     c["category"],
                }
                for c in batch
            ],
        )
        print(f"  Indexados {min(i + BATCH_SIZE, total)}/{total}", end="\r")

    print(f"\n\nFin. Colección '{COLLECTION_NAME}': {collection.count()} documentos.")


if __name__ == "__main__":
    rebuild_index()

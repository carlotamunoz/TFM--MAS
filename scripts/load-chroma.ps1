param([switch]$Clean = $false)
$ErrorActionPreference = "Continue"

Write-Host "========================================================"
Write-Host "Cargando documentos AJP en ChromaDB"
Write-Host "========================================================"

Write-Host "Instalando dependencias..."
docker exec sa_api pip install pypdf langchain langchain-community langchain-text-splitters --break-system-packages -q *>$null

if ($Clean) {
    Write-Host "Limpiando ChromaDB..."
    docker exec sa_api bash -c "rm -rf /app/rag/data/chroma/*; mkdir -p /app/rag/data/chroma" *>$null
}

Write-Host "Cargando PDFs..."

$pythonScript = "
import os, sys
from pathlib import Path
raw_dir = Path('/app/rag/data/raw')
pdfs = sorted(raw_dir.glob('*.pdf'))
print(f'PDFs: {len(pdfs)}')
for pdf in pdfs:
    print(f'  - {pdf.name}')
if not pdfs:
    sys.exit(1)
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.embeddings import HuggingFaceEmbeddings
client = chromadb.PersistentClient(path='/app/rag/data/chroma')
collection = client.get_or_create_collection('ajp_doctrine_chunks')
embeddings = HuggingFaceEmbeddings(model_name='sentence-transformers/all-MiniLM-L6-v2')
splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)


for pdf in pdfs:
    print(f'Cargando {pdf.name}...')
    loader = PyPDFLoader(str(pdf))
    docs = loader.load()
    chunks = splitter.split_documents(docs)
    import re
    match = re.search(r'AJP[_-](\d+)[_-](\d+)', pdf.stem, re.IGNORECASE)
    ajp_version = f'AJP-{match.group(1)}.{match.group(2)}' if match else pdf.stem
    for i, chunk in enumerate(chunks):
        collection.add(
            documents=[chunk.page_content],
            metadatas=[{'source_doc': ajp_version, 'page': str(chunk.metadata.get('page', '0'))}],
            ids=[f'{ajp_version}_{i}_{abs(hash(chunk.page_content)) % 1000000}']
        )
    print(f'  OK: {len(chunks)} chunks de {ajp_version}')
print(f'DONE: {collection.count()} total')
"

docker exec sa_api python3 -c $pythonScript

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Reiniciando API..."
    docker restart sa_api
    Start-Sleep -Seconds 5
    Write-Host "OK - Cargado" -ForegroundColor Green
} else {
    Write-Host "ERROR" -ForegroundColor Red
    exit 1
}
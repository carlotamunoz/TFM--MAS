#!/usr/bin/env python3
"""
Ejecuta el conjunto doctrine_only contra el sistema y ensambla el dataset
de evaluacion para RAGAS.

Para cada pregunta:
  - recupera los chunks con el mismo DoctrineRetriever del pipeline
    (mismo modelo multilingue, mismo filtro por dominio) -> retrieved_contexts
  - llama al endpoint /query para obtener la respuesta del sistema -> answer
  - combina con el ground truth (reference, reference_contexts)

Salida: doctrine_eval_dataset.json, listo para el script de scoring RAGAS.

Se ejecuta DENTRO del contenedor sa_api:
    docker cp run_doctrine_eval.py sa_api:/tmp/
    docker cp ground_truth_doctrine.json sa_api:/tmp/
    docker exec sa_api python3 /tmp/run_doctrine_eval.py
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/app")

GROUND_TRUTH_PATH = "/tmp/ground_truth_doctrine_v2.json"
OUTPUT_PATH = "/tmp/doctrine_eval_dataset.json"
API_BASE = "http://localhost:8000"
TOP_K = 6

# Mapeo dominio -> source_doc, identico al del pipeline
DOMAIN_TO_SOURCE = {"air": "AJP-3.3", "land": "AJP-3.2", "maritime": "AJP-3.1"}


def http_post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_retrieved_contexts(retriever, query, domain):
    """Recupera los chunks igual que el pipeline y devuelve la lista de textos."""
    source_doc = DOMAIN_TO_SOURCE.get(domain)
    filter_meta = {"source_doc": source_doc} if source_doc else None
    result = retriever.retrieve(query=query, k=TOP_K, filter_meta=filter_meta)
    docs = result.get("docs_final", [])
    return [d.page_content for d in docs]


def main():
    from tools.doctrine_retriever import DoctrineRetriever

    retriever = DoctrineRetriever(
        chroma_dir="/app/rag/data/chroma",
        collection="ajp_doctrine_chunks",
        embedding_model="paraphrase-multilingual-mpnet-base-v2",
    )

    with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
        ground_truth = json.load(f)

    dataset = []
    for gt in ground_truth:
        qid = gt["id"]
        query = gt["user_input"]
        domain = gt["domain"]
        print(f"Procesando {qid} [dominio={domain}]...", flush=True)

        # 1. Recuperar contextos (mismos chunks que usa el pipeline)
        retrieved = get_retrieved_contexts(retriever, query, domain)

        # 2. Obtener respuesta del sistema via API
        login = http_post("/login", {"operator_id": "op01", "domain": domain})
        session_id = login.get("session_id")

        t0 = time.time()
        resp = http_post("/query", {"query": query},
                         headers={"X-Session-ID": session_id})
        latency_ms = int((time.time() - t0) * 1000)

        answer = resp.get("response", {}).get("response_text", "")
        trace = resp.get("pipeline_trace", {}) or {}
        synth = (trace.get("synthesizer") or {}).get("output", {}) or {}
        degraded = synth.get("degraded") if isinstance(synth, dict) else None

        dataset.append({
            "id": qid,
            "domain": domain,
            "source_doc": gt["source_doc"],
            "type": gt["type"],
            "difficulty": gt["difficulty"],
            "user_input": query,
            "response": answer,
            "retrieved_contexts": retrieved,
            "reference": gt["reference"],
            "reference_contexts": gt["reference_contexts"],
            "system_degraded": degraded,
            "n_retrieved": len(retrieved),
            "latency_ms": latency_ms,
        })

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"\nDataset de evaluacion guardado en {OUTPUT_PATH}")
    print(f"Total preguntas: {len(dataset)}")
    for d in dataset:
        print(f"  {d['id']}: retrieved={d['n_retrieved']} "
              f"answer_len={len(d['response'])} degraded={d['system_degraded']}")


if __name__ == "__main__":
    main()
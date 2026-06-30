#!/usr/bin/env python3
"""
Ejecuta el ciclo completo de evaluacion hibrida 3 veces y promedia con desviacion.

Por cada pasada:
  1. lanza las 10 preguntas contra el sistema (respuesta + categoria + degraded +
     deteccion de si el dato de grafo aparecio)
  2. ejecuta el juez de integracion sobre las respuestas de esa pasada

Al final promedia las tres dimensiones del juez (graph_accuracy,
doctrine_grounding, integration_coherence) por pregunta y global, con media y
desviacion tipica, y reporta tambien la estabilidad de clasificacion.

Se ejecuta DENTRO del contenedor sa_api.
Requiere: /tmp/ground_truth_hybrid.json, /tmp/judge_integration.py, OPENAI_API_KEY

Uso:
    python3 run_hybrid_3x.py [n_pasadas]   (por defecto 3)
"""
import json
import os
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, "/app")

GROUND_TRUTH_PATH = "/tmp/ground_truth_hybrid.json"
API_BASE = "http://localhost:8000"
TOP_K = 6
DOMAIN_TO_SOURCE = {"air": "AJP-3.3", "land": "AJP-3.2", "maritime": "AJP-3.1"}
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-4.1")
DIMS = ["graph_accuracy", "doctrine_grounding", "integration_coherence"]


def http_post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_retrieved(retriever, query, domain):
    src = DOMAIN_TO_SOURCE.get(domain)
    fm = {"source_doc": src} if src else None
    res = retriever.retrieve(query=query, k=TOP_K, filter_meta=fm)
    return [d.page_content for d in res.get("docs_final", [])]


def run_system_pass(ground_truth, retriever):
    """Una pasada completa de las 10 preguntas contra el sistema."""
    dataset = []
    for gt in ground_truth:
        domain = gt["domain"]
        query = gt["user_input"]
        retrieved = get_retrieved(retriever, query, domain)
        login = http_post("/login", {"operator_id": "op01", "domain": domain})
        sid = login.get("session_id")
        t0 = time.time()
        resp = http_post("/query", {"query": query}, headers={"X-Session-ID": sid})
        latency = int((time.time() - t0) * 1000)

        answer = resp.get("response", {}).get("response_text", "")
        trace = resp.get("pipeline_trace", {}) or {}
        clf = (trace.get("classifier") or {}).get("output", {}) or {}
        category = clf.get("category") if isinstance(clf, dict) else None
        syn = (trace.get("synthesizer") or {}).get("output", {}) or {}
        degraded = syn.get("degraded") if isinstance(syn, dict) else None
        pattern = syn.get("pattern") if isinstance(syn, dict) else None

        dataset.append({
            "id": gt["id"], "domain": domain, "source_doc": gt["source_doc"],
            "type": gt["type"], "difficulty": gt["difficulty"],
            "user_input": query, "response": answer,
            "retrieved_contexts": retrieved, "reference": gt["reference"],
            "reference_contexts": [],
            "graph_component": gt["graph_component"],
            "doctrine_component": gt["doctrine_component"],
            "system_category": category, "system_pattern": pattern,
            "system_degraded": degraded,
            "n_retrieved": len(retrieved), "latency_ms": latency,
        })
    return dataset


def run_judge(dataset):
    """Ejecuta el juez de integracion sobre un dataset y devuelve scores por id."""
    from openai import OpenAI
    import evaluacion.judge_integration as J  # reutiliza rubrica y prompt

    client = OpenAI()
    scores = {}
    for item in dataset:
        prompt = J.build_prompt(item)
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[{"role": "system", "content": J.RUBRIC},
                      {"role": "user", "content": prompt}],
            temperature=0.0,
        )
        sc = J.parse_scores(resp.choices[0].message.content) or {}
        scores[item["id"]] = {d: sc.get(d) for d in DIMS}
    return scores


def main():
    n_pass = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    sys.path.insert(0, "/tmp")  # para importar judge_integration

    from tools.doctrine_retriever import DoctrineRetriever
    retriever = DoctrineRetriever(
        chroma_dir="/app/rag/data/chroma",
        collection="ajp_doctrine_chunks",
        embedding_model="paraphrase-multilingual-mpnet-base-v2",
    )

    with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
        ground_truth = json.load(f)

    all_scores = []      # lista de dicts {id: {dim: val}}
    all_class = []       # lista de dicts {id: (category, pattern, degraded)}
    for p in range(n_pass):
        print(f"\n{'='*60}\nPASADA {p+1}/{n_pass} — ejecutando sistema...", flush=True)
        dataset = run_system_pass(ground_truth, retriever)
        with open(f"/tmp/hybrid_dataset_pass{p+1}.json", "w", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)

        class_info = {d["id"]: (d["system_category"], d["system_pattern"],
                                d["system_degraded"]) for d in dataset}
        all_class.append(class_info)
        for d in dataset:
            print(f"  {d['id']}: cat={d['system_category']} "
                  f"pattern={d['system_pattern']} degraded={d['system_degraded']}")

        print(f"PASADA {p+1}/{n_pass} — ejecutando juez...", flush=True)
        scores = run_judge(dataset)
        all_scores.append(scores)
        for qid, sc in scores.items():
            print(f"  {qid}: grafo={sc['graph_accuracy']} "
                  f"doctrina={sc['doctrine_grounding']} "
                  f"integracion={sc['integration_coherence']}")

    # --- Promediar scores del juez por pregunta ---
    ids = [gt["id"] for gt in ground_truth]
    print(f"\n{'='*60}\n=== MEDIAS POR PREGUNTA ({n_pass} pasadas) ===")
    per_q = []
    for qid in ids:
        row = {"id": qid}
        for dim in DIMS:
            vals = [s[qid][dim] for s in all_scores
                    if isinstance(s[qid][dim], (int, float))]
            if vals:
                row[dim + "_mean"] = round(statistics.mean(vals), 3)
                row[dim + "_std"] = round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0
            else:
                row[dim + "_mean"], row[dim + "_std"] = None, None
        per_q.append(row)
        print(f"  {qid}: "
              f"grafo={row['graph_accuracy_mean']}±{row['graph_accuracy_std']} "
              f"doctrina={row['doctrine_grounding_mean']}±{row['doctrine_grounding_std']} "
              f"integracion={row['integration_coherence_mean']}±{row['integration_coherence_std']}")

    # --- Medias globales ---
    print(f"\n=== MEDIAS GLOBALES ({n_pass} pasadas) ===")
    for dim in DIMS:
        means = [r[dim + "_mean"] for r in per_q if isinstance(r[dim + "_mean"], (int, float))]
        gmean = round(statistics.mean(means), 3) if means else None
        gstd = round(statistics.pstdev(means), 3) if len(means) > 1 else 0.0
        print(f"  {dim}: {gmean} (desv. entre preguntas {gstd})")

    # --- Estabilidad de clasificacion ---
    print(f"\n=== ESTABILIDAD DE CLASIFICACION ({n_pass} pasadas) ===")
    correct_cat = "ontology_with_context"
    for qid in ids:
        cats = [c[qid][0] for c in all_class]
        n_correct = sum(1 for c in cats if c == correct_cat)
        estable = "estable" if len(set(cats)) == 1 else "VARIABLE"
        print(f"  {qid}: {n_correct}/{n_pass} correctas [{estable}] {cats}")

    # Guardar consolidado
    out = {"n_pass": n_pass, "per_question": per_q,
           "classification": {qid: [c[qid] for c in all_class] for qid in ids}}
    with open("/tmp/hybrid_3x_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nConsolidado guardado en /tmp/hybrid_3x_results.json")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Calcula metricas RAGAS sobre el dataset de evaluacion doctrine_only.

Metricas:
  - faithfulness:        la respuesta esta soportada por los chunks recuperados
  - answer_relevancy:    la respuesta es pertinente a la pregunta
  - context_precision:   los chunks relevantes estan bien rankeados (usa reference)
  - context_recall:      se recupero todo lo necesario (usa reference)
  - answer_correctness:  correccion factual/semantica frente a la referencia
  - semantic_similarity: similitud semantica respuesta vs referencia

Requiere OPENAI_API_KEY en el entorno (RAGAS usa un juez LLM y embeddings).

Uso:
    pip install ragas datasets langchain-openai
    export OPENAI_API_KEY=...
    python3 score_doctrine_ragas.py /ruta/doctrine_eval_dataset.json
"""
import json
import sys

import pandas as pd
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    answer_correctness,
    answer_relevancy,
    answer_similarity,
    context_precision,
    context_recall,
    faithfulness,
)

# En RAGAS 0.1.x la metrica de similitud semantica se llama answer_similarity.
# Se alias para mantener un nombre uniforme en el resto del script.
semantic_similarity = answer_similarity


def load_dataset(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # RAGAS espera estos nombres de campo
    records = {
        "question": [],
        "answer": [],
        "contexts": [],
        "ground_truth": [],
        "reference_contexts": [],
    }
    meta = []
    for d in data:
        records["question"].append(d["user_input"])
        records["answer"].append(d["response"])
        records["contexts"].append(d["retrieved_contexts"])
        records["ground_truth"].append(d["reference"])
        records["reference_contexts"].append(d["reference_contexts"])
        meta.append({
            "id": d["id"],
            "domain": d["domain"],
            "type": d["type"],
            "difficulty": d["difficulty"],
            "n_retrieved": d["n_retrieved"],
            "system_degraded": d.get("system_degraded"),
            "latency_ms": d.get("latency_ms"),
        })
    return Dataset.from_dict(records), meta


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "doctrine_eval_dataset.json"
    dataset, meta = load_dataset(path)

    metrics = [
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall,
        answer_correctness,
        semantic_similarity,
    ]

    print("Evaluando con RAGAS (esto consume llamadas a la API del juez)...\n")
    result = evaluate(dataset, metrics=metrics)

    # Resultados por pregunta
    df = result.to_pandas()
    meta_df = pd.DataFrame(meta)
    full = pd.concat([meta_df, df], axis=1)

    # RAGAS 0.1.x nombra algunas columnas distinto (answer_similarity en vez de
    # semantic_similarity). Se detectan dinamicamente las columnas de metricas
    # presentes en el DataFrame de resultados.
    candidate_cols = [
        "faithfulness", "answer_relevancy", "context_precision",
        "context_recall", "answer_correctness",
        "answer_similarity", "semantic_similarity",
    ]
    metric_cols = [c for c in candidate_cols if c in df.columns]

    # Tabla por pregunta
    print("\n=== Resultados por pregunta ===")
    cols_show = ["id", "type", "difficulty"] + metric_cols
    print(full[cols_show].round(3).to_string(index=False))

    # Medias globales
    print("\n=== Medias globales ===")
    for m in metric_cols:
        if m in full.columns:
            print(f"  {m:22s}: {full[m].mean():.3f}")

    # Medias por dificultad
    print("\n=== Medias por dificultad ===")
    by_diff = full.groupby("difficulty")[metric_cols].mean().round(3)
    print(by_diff.to_string())

    # Medias por tipo
    print("\n=== Medias por tipo de pregunta ===")
    by_type = full.groupby("type")[metric_cols].mean().round(3)
    print(by_type.to_string())

    # Guardar CSV para apendice
    full.to_csv("doctrine_ragas_results.csv", index=False)
    print("\nResultados completos guardados en doctrine_ragas_results.csv")


if __name__ == "__main__":
    main()
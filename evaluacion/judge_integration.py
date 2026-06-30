#!/usr/bin/env python3
"""
Juez de integracion (LLM-as-judge) para la categoria ontology_with_context.

Evalua tres dimensiones independientes (0.0 a 1.0) de la respuesta del sistema
a una pregunta hibrida:

  1. graph_accuracy       - contiene el dato correcto del grafo
  2. doctrine_grounding   - enmarca correctamente la doctrina sin fabricar
  3. integration_coherence - conecta ambas fuentes de forma coherente
                             (mas que la yuxtaposicion de las dos partes)

Requiere OPENAI_API_KEY. Usa el mismo proveedor que el sistema.

Uso:
    python3 judge_integration.py /tmp/hybrid_eval_dataset.json
"""
import json
import os
import sys
import re

from openai import OpenAI

JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-4.1")

RUBRIC = """Eres un evaluador experto en sistemas de recuperacion de informacion para el
dominio militar. Evalua la RESPUESTA del sistema a una pregunta que requiere combinar
dos fuentes: datos de un grafo de conocimiento (estado del entorno) y doctrina militar
OTAN (marco de actuacion).

Puntua TRES dimensiones independientes de 0.0 a 1.0:

1. graph_accuracy: la respuesta contiene el dato concreto y correcto del grafo.
   - 1.0 si el hecho del grafo aparece correcto y completo.
   - 0.5 si aparece parcial o impreciso.
   - 0.0 si falta o es incorrecto.

2. doctrine_grounding: la respuesta enmarca el concepto segun la doctrina de forma
   correcta y sin inventar afirmaciones no respaldadas.
   - 1.0 si el encuadre doctrinal es correcto y fiel.
   - 0.5 si es vago o parcialmente correcto.
   - 0.0 si es incorrecto o fabricado.

3. integration_coherence: la respuesta CONECTA el dato del grafo con la doctrina de
   forma que el resultado sea mas que la suma de las partes. Una respuesta que expone
   el dato y, por separado, recita doctrina sin vincularlos, puntua bajo. Una que
   explica como ese dato concreto encaja o se interpreta a la luz de la doctrina,
   puntua alto.
   - 1.0 si integra ambas fuentes en una interpretacion coherente y util.
   - 0.5 si las menciona ambas pero la conexion es debil o implicita.
   - 0.0 si solo aborda una fuente, o las yuxtapone sin vincularlas.

Responde UNICAMENTE con un objeto JSON valido, sin texto adicional, con esta forma:
{"graph_accuracy": 0.0, "doctrine_grounding": 0.0, "integration_coherence": 0.0, "justificacion": "breve"}"""


def build_prompt(item):
    gc = item["graph_component"]
    dc = item["doctrine_component"]
    return f"""PREGUNTA:
{item['user_input']}

GROUND TRUTH:
- Hecho verificable del grafo: {gc['verifiable_fact']}
- Marco doctrinal esperado: {dc['summary']}
- Respuesta de referencia: {item['reference']}

RESPUESTA DEL SISTEMA A EVALUAR:
{item['response']}

Evalua las tres dimensiones segun la rubrica."""


def parse_scores(text):
    # Extraer el primer objeto JSON de la respuesta
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/hybrid_eval_dataset.json"
    with open(path, encoding="utf-8") as f:
        dataset = json.load(f)

    client = OpenAI()
    results = []

    for item in dataset:
        prompt = build_prompt(item)
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": RUBRIC},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
        scores = parse_scores(resp.choices[0].message.content)
        if scores is None:
            print(f"  {item['id']}: ERROR parseando respuesta del juez")
            scores = {"graph_accuracy": None, "doctrine_grounding": None,
                      "integration_coherence": None, "justificacion": "parse error"}
        results.append({
            "id": item["id"],
            "domain": item["domain"],
            "type": item["type"],
            "difficulty": item["difficulty"],
            **{k: scores.get(k) for k in
               ["graph_accuracy", "doctrine_grounding", "integration_coherence"]},
            "justificacion": scores.get("justificacion", ""),
        })
        sc = results[-1]
        print(f"  {item['id']}: grafo={sc['graph_accuracy']} "
              f"doctrina={sc['doctrine_grounding']} "
              f"integracion={sc['integration_coherence']}")

    with open("/tmp/hybrid_integration_scores.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Medias
    def avg(key):
        vals = [r[key] for r in results if isinstance(r[key], (int, float))]
        return sum(vals) / len(vals) if vals else 0.0

    print("\n=== Medias de la metrica de integracion ===")
    print(f"  graph_accuracy:        {avg('graph_accuracy'):.3f}")
    print(f"  doctrine_grounding:    {avg('doctrine_grounding'):.3f}")
    print(f"  integration_coherence: {avg('integration_coherence'):.3f}")
    print("\nGuardado en /tmp/hybrid_integration_scores.json")


if __name__ == "__main__":
    main()
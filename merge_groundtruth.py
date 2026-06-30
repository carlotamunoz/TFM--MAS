"""
Fusiona el ground truth original con los reference_contexts basados en los
chunks reales del indice. Deduplica chunks repetidos dentro de una pregunta.

Se ejecuta DENTRO del contenedor. Lee:
  /tmp/ground_truth_doctrine.json        (subido previamente)
  /tmp/chunk_reference_contexts.json     (generado por build_chunk_groundtruth_v2)
Escribe:
  /tmp/ground_truth_doctrine_v2.json
"""
import json

with open("/tmp/ground_truth_doctrine.json", encoding="utf-8") as f:
    gt = json.load(f)

with open("/tmp/chunk_reference_contexts.json", encoding="utf-8") as f:
    chunk_ctx = json.load(f)

for item in gt:
    qid = item["id"]
    contexts = chunk_ctx.get(qid, [])
    seen = set()
    deduped = []
    for c in contexts:
        key = c[:100]
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    item["reference_contexts"] = deduped

with open("/tmp/ground_truth_doctrine_v2.json", "w", encoding="utf-8") as f:
    json.dump(gt, f, ensure_ascii=False, indent=2)

print("Ground truth v2 generado (reference_contexts = chunks reales del indice)\n")
for item in gt:
    print(f"  {item['id']}: {len(item['reference_contexts'])} reference_context(s) "
          f"[{item['type']}/{item['difficulty']}]")
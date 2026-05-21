#!/bin/bash
# scripts/entrypoint.sh
# Espera a Fuseki, carga la ontología si el grafo está vacío y arranca la API.
set -e

FUSEKI_BASE="${FUSEKI_BASE:-http://fuseki:3030}"
DATASET="${FUSEKI_DATASET:-dron}"
NAMED_GRAPH="${FUSEKI_GRAPH:-urn:scenario:static}"
OWL_FILE="/staging/ontologia.ttl"

# ── 1. Esperar a Fuseki ──────────────────────────────────────────────
echo "[init] Esperando a Fuseki en ${FUSEKI_BASE}..."
until curl -sf "${FUSEKI_BASE}/\$/ping" > /dev/null 2>&1; do
  sleep 2
done
echo "[init] Fuseki listo."

# ── 2. Comprobar si el grafo nombrado tiene datos ────────────────────
COUNT=$(curl -s "${FUSEKI_BASE}/${DATASET}/query" \
  --data-urlencode "query=SELECT (COUNT(*) AS ?n) WHERE { GRAPH <${NAMED_GRAPH}> { ?s ?p ?o } }" \
  -H "Accept: application/sparql-results+json" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['results']['bindings'][0]['n']['value'])" 2>/dev/null || echo "0")

if [ "$COUNT" = "0" ]; then
  echo "[init] Grafo <${NAMED_GRAPH}> vacío."

  if [ -f "$OWL_FILE" ]; then
    echo "[init] Cargando ontología desde ${OWL_FILE}..."

    # El fichero exportado de Fuseki es N-Quads (contiene el grafo nombrado).
    # Lo cargamos directamente al dataset; Fuseki restaura los grafos nombrados.
    HTTP_CODE=$(curl -s -o /tmp/fuseki_upload.log -w "%{http_code}" \
      -u "admin:${FUSEKI_ADMIN_PASSWORD:-admin}" \
      -X PUT "${FUSEKI_BASE}/${DATASET}/data?graph=${NAMED_GRAPH}" \
      -H "Content-Type: application/n-triples" \
      --data-binary "@${OWL_FILE}")

    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "204" ]; then
      echo "[init] Ontología cargada correctamente (HTTP ${HTTP_CODE})."
    else
      echo "[init] ERROR al cargar ontología (HTTP ${HTTP_CODE}):"
      cat /tmp/fuseki_upload.log
      exit 1
    fi

    echo "[init] Ontología cargada correctamente."
  else
    echo "[init] AVISO: no se encontró el fichero de ontología en ${OWL_FILE}."
    echo "[init] Monta tu fichero OWL/TTL con:"
    echo "[init]   -v /ruta/a/ontologia.ttl:/staging/ontologia.ttl"
  fi
else
  echo "[init] Grafo <${NAMED_GRAPH}> tiene ${COUNT} triples. OK."
fi

# ── 3. Arrancar la API ───────────────────────────────────────────────
echo "[init] Arrancando API en 0.0.0.0:8000..."
exec uvicorn api:app --host 0.0.0.0 --port 8000

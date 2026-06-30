#!/usr/bin/env bash
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  scripts/entrypoint.sh
#  Se ejecuta DENTRO del contenedor Â«apiÂ». Hace que el sistema sea
#  plug-and-play: arranca solo, sin scripts manuales.
#    1) Espera a que Fuseki responda
#    2) Crea el dataset si no existe (idempotente)
#    3) Carga la ontologÃ­a en el grafo nombrado si estÃ¡ vacÃ­o
#    4) Lanza la API (uvicorn)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
set -e

FUSEKI_BASE="${FUSEKI_BASE:-http://fuseki:3030}"
DATASET="${FUSEKI_DATASET:-dron}"
NAMED_GRAPH="${FUSEKI_GRAPH:-urn:scenario:static}"
ADMIN_PWD="${FUSEKI_ADMIN_PASSWORD:-admin}"
OWL_FILE="${OWL_FILE:-/app/ontologia/ontologia.ttl}"

# â”€â”€ 1. Esperar a Fuseki â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo "[init] Esperando a Fuseki en ${FUSEKI_BASE} ..."
until curl -sf "${FUSEKI_BASE}/\$/ping" >/dev/null 2>&1; do
  sleep 2
done
echo "[init] Fuseki listo."

# â”€â”€ 2. Crear el dataset si no existe â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if ! curl -sf -u "admin:${ADMIN_PWD}" \
        "${FUSEKI_BASE}/\$/datasets/${DATASET}" >/dev/null 2>&1; then
  echo "[init] Creando dataset '${DATASET}' (TDB2) ..."
  curl -s -u "admin:${ADMIN_PWD}" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data "dbType=tdb2&dbName=${DATASET}" \
    "${FUSEKI_BASE}/\$/datasets" >/dev/null || true
fi

# â”€â”€ 3. Cargar la ontologÃ­a solo si el grafo estÃ¡ vacÃ­o â”€â”€â”€â”€â”€â”€â”€â”€â”€
COUNT=$(curl -s "${FUSEKI_BASE}/${DATASET}/query" \
  --data-urlencode "query=SELECT (COUNT(*) AS ?n) WHERE { GRAPH <${NAMED_GRAPH}> { ?s ?p ?o } }" \
  -H "Accept: application/sparql-results+json" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['results']['bindings'][0]['n']['value'])" 2>/dev/null || echo "0")

if [ "$COUNT" = "0" ]; then
  if [ -f "$OWL_FILE" ]; then
    echo "[init] Grafo <${NAMED_GRAPH}> vacÃ­o. Cargando ontologÃ­a desde ${OWL_FILE} ..."
    curl -sf -u "admin:${ADMIN_PWD}" \
      -X PUT "${FUSEKI_BASE}/${DATASET}/data?graph=${NAMED_GRAPH}" \
      -H "Content-Type: text/turtle" \
      --data-binary "@${OWL_FILE}"
    echo "[init] OntologÃ­a cargada correctamente."
  else
    echo "[init] AVISO: no se encontrÃ³ el fichero de ontologÃ­a en ${OWL_FILE}."
  fi
else
  echo "[init] Grafo <${NAMED_GRAPH}> ya contiene ${COUNT} triples. No se recarga."
fi

# â”€â”€ 4. Arrancar la API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo "[init] Arrancando API en 0.0.0.0:8000 ..."
exec uvicorn api:app --host 0.0.0.0 --port 8000
$ErrorActionPreference = "Stop"

Write-Host "Esperando a que Fuseki esté listo..."
for ($i = 1; $i -le 30; $i++) {
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:3030/$/ping" -UseBasicParsing -TimeoutSec 2
        Write-Host "✓ Fuseki respondiendo"
        break
    } catch {
        Write-Host "  Intento $i/30..."
        Start-Sleep -Seconds 1
    }
}

Write-Host "Creando dataset dron..."
docker exec sa_fuseki curl -s -u admin:admin `
  -H "Content-Type: application/x-www-form-urlencoded" `
  --data "dbType=tdb2&dbName=dron" `
  http://localhost:3030/$/datasets

Write-Host "Cargando ontología (1158 triples)..."
docker exec sa_fuseki curl -s -u admin:admin `
  -H "Content-Type: text/turtle" `
  --data-binary @/staging/ontologia/ontologia.ttl `
  "http://localhost:3030/dron/data?graph=urn:scenario:static"

Write-Host ""
Write-Host "✓ Sistema listo"
Write-Host ""
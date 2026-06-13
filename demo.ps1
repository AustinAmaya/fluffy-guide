# One-command demo: seed a lore database with the six Boxwell stories and
# launch the visualizer. Run from the lore-stack directory:
#   powershell -ExecutionPolicy Bypass -File demo.ps1
param(
    [int]$Port = 8377,
    [string]$Db = "demo\lore.db"
)
$py = if (Test-Path '.\.venv\Scripts\python.exe') { '.\.venv\Scripts\python.exe' } else { 'python' }

New-Item -ItemType Directory -Force (Split-Path $Db) | Out-Null
if (Test-Path $Db) { Remove-Item $Db -Force }

& $py -m lore_stack.cli init-db --db $Db
Get-ChildItem "tests\fixtures\stories\*.delta.json" | Sort-Object Name | ForEach-Object {
    Write-Host ("ingesting " + $_.Name)
    & $py -m lore_stack.cli ingest-delta --db $Db --file $_.FullName | Out-Null
}

Write-Host ""
Write-Host "Demo lore database ready: $Db"
Write-Host "  - Boxwell is canon (corroborated across stories)"
Write-Host "  - one OPEN CONFLICT (a story claims he's a baker)"
Write-Host "  - one MOTIF (the 'Mayor of the Mantelpiece' joke - never canon)"
Write-Host ""
Write-Host "Try in the query box:  Tell another story with Boxwell"
Write-Host "Or, with no name hit:  Tell another story about the travelling clockmaker"
Write-Host ""
& $py -m lore_stack.cli serve --db $Db --port $Port

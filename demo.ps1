# One-command demo: build a lore home with a seeded testing lore plus an empty
# production lore, then launch the visualizer with the lore switcher enabled.
#   powershell -ExecutionPolicy Bypass -File demo.ps1
param(
    [int]$Port = 8377,
    [string]$Home = "demo\lores"
)
$py = if (Test-Path '.\.venv\Scripts\python.exe') { '.\.venv\Scripts\python.exe' } else { 'python' }

if (Test-Path $Home) { Remove-Item -Recurse -Force $Home }
& $py -m lore_stack.cli lores create --home $Home --name production
& $py -m lore_stack.cli lores create --home $Home --name test-boxwell
& $py -m lore_stack.cli lores create --home $Home --name test-sandbox

Get-ChildItem "tests\fixtures\stories\*.delta.json" | Sort-Object Name | ForEach-Object {
    Write-Host ("ingesting into test-boxwell: " + $_.Name)
    & $py -m lore_stack.cli ingest-delta --db "$Home\test-boxwell.db" --file $_.FullName | Out-Null
}

Write-Host ""
Write-Host "Lore home ready: $Home"
Write-Host "  production    - empty, your real canon goes here"
Write-Host "  test-boxwell  - seeded: canon Boxwell, one open conflict, one motif"
Write-Host "  test-sandbox  - empty scratch space"
Write-Host ""
Write-Host "Switch lores with the dropdown in the header; '+ lore' creates more."
Write-Host "Try the query:  Tell another story with Boxwell and Mirel at Whitmoor"
Write-Host ""
& $py -m lore_stack.cli serve --home $Home --port $Port

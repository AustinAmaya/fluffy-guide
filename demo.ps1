# One-command demo: build a lore home with a seeded 'test-boxwell' lore (some
# stories committed as canon, others left in the review inbox), an empty
# 'production' lore, and a scratch lore. Then launch the visualizer.
#   powershell -ExecutionPolicy Bypass -File demo.ps1
param(
    [int]$Port = 8377,
    [string]$LoreHome = "demo\lores"
)
$py = if (Test-Path '.\.venv\Scripts\python.exe') { '.\.venv\Scripts\python.exe' } else { 'python' }
$fixtures = "tests\fixtures\stories"

if (Test-Path $LoreHome) { Remove-Item -Recurse -Force $LoreHome }
Remove-Item -Recurse -Force "demo\.snapshots" -ErrorAction SilentlyContinue
foreach ($name in @("production", "test-boxwell", "harrow-hollow", "clockwork-coast", "winnie-the-pooh")) {
    & $py -m lore_stack.cli lores create --home $LoreHome --name $name
}
$db = "$LoreHome\test-boxwell.db"

# Commit stories 1-4 directly (Boxwell becomes canon; story 5 is a contradiction,
# story 6 is the motif). Then leave stories 5 and 6 in the REVIEW INBOX so the
# inbox panel has proposals to downselect.
foreach ($n in 1..4) {
    & $py -m lore_stack.cli ingest-delta --db $db --file ("$fixtures\boxwell_story_{0:d2}.delta.json" -f $n) | Out-Null
}
foreach ($n in 5..6) {
    & $py -m lore_stack.cli stage-story --db $db --file ("$fixtures\boxwell_story_{0:d2}.md" -f $n) --fixtures $fixtures
}

# Extended worlds from committed delta fixtures: two larger Boxwell worlds plus a
# hand-authored chapter-1 Winnie-the-Pooh lore.
foreach ($lore in @("harrow-hollow", "clockwork-coast", "winnie-the-pooh")) {
    Get-ChildItem "examples\lores\$lore\*.delta.json" | Sort-Object Name | ForEach-Object {
        & $py -m lore_stack.cli ingest-delta --db "$LoreHome\$lore.db" --file $_.FullName | Out-Null
    }
}

# Freeze the seed lores: a pristine baseline (DB + history) the operator can play
# with and hard-reset to. 'production' stays unfrozen (it's the working lore).
foreach ($lore in @("test-boxwell", "harrow-hollow", "clockwork-coast", "winnie-the-pooh")) {
    & $py -m lore_stack.cli lores freeze --home $LoreHome --name $lore | Out-Null
}

Write-Host ""
Write-Host "Lore home ready: $LoreHome"
Write-Host "  production       - empty, your real canon goes here (not frozen)"
Write-Host "  test-boxwell     - canon Boxwell (stories 1-4) + 2 proposals in the review inbox (~5 nodes)"
Write-Host "  harrow-hollow    - a winter village world (~10 nodes)"
Write-Host "  clockwork-coast  - a coastal clock-tower world (~20 nodes)"
Write-Host "  winnie-the-pooh  - hand-authored from chapter 1 of A. A. Milne (~11 nodes)"
Write-Host ""
Write-Host "The four seed lores are FROZEN: play freely, then 'reset to frozen' to restore."
Write-Host ""
Write-Host "In the UI: switch lores (dropdown), review the inbox proposals (downselect"
Write-Host "and apply, or discard), then check the History panel to roll anything back."
Write-Host "Query box:  Tell another story with Boxwell and Mirel at Whitmoor"
Write-Host ""
& $py -m lore_stack.cli serve --home $LoreHome --port $Port

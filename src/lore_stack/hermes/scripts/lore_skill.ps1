# Thin Hermes skill shell over the lore-stack CLI. No lore logic lives here.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('init-db', 'ingest-delta', 'compile-context')]
    [string]$Command,
    [Parameter(Mandatory = $true)][string]$DbPath,
    [string]$Query,
    [string]$File,
    [string]$Out
)
$py = if ($env:LORE_STACK_PYTHON) { $env:LORE_STACK_PYTHON } else { 'python' }
switch ($Command) {
    'init-db'         { & $py -m lore_stack.cli init-db --db $DbPath }
    'ingest-delta'    { & $py -m lore_stack.cli ingest-delta --db $DbPath --file $File }
    'compile-context' { & $py -m lore_stack.cli compile-context --db $DbPath --query $Query --out $Out }
}
exit $LASTEXITCODE

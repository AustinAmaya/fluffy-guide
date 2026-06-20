# Thin Hermes skill shell over the lore-stack CLI (storage half). No lore logic here.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('init-db', 'ingest-delta', 'stage-delta', 'compile-context')]
    [string]$Command,
    [string]$DbPath,
    [string]$Query,
    [string]$File,
    [string]$Out,
    [switch]$Canon,
    [string]$Embedder
)
$py = if ($env:LORE_STACK_PYTHON) { $env:LORE_STACK_PYTHON } else { 'python' }
$emb = if ($Embedder) { @('--embedder', $Embedder) } else { @() }
# -DbPath optional: when omitted the CLI falls back to $LORE_STACK_DB.
$db = if ($DbPath) { @('--db', $DbPath) } else { @() }
switch ($Command) {
    'init-db'         { & $py -m lore_stack.cli init-db @db }
    'ingest-delta'    {
        $canonArg = if ($Canon) { @('--canon') } else { @() }
        & $py -m lore_stack.cli ingest-delta @db --file $File @canonArg @emb
    }
    'stage-delta'     { & $py -m lore_stack.cli stage-delta @db --file $File }
    'compile-context' { & $py -m lore_stack.cli compile-context @db --query $Query --out $Out @emb }
}
exit $LASTEXITCODE

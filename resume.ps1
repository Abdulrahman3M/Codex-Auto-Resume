$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot

$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $launcher) {
    & py -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'
    if ($LASTEXITCODE -eq 0) {
        & py -3 -m codex_resume @args
        exit $LASTEXITCODE
    }
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    Write-Error 'Python 3 was not found. Install Python 3.9 or later.'
    exit 127
}
& python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Python version is too old. Python 3.9 or later is required.'
    exit 2
}
& python -m codex_resume @args
exit $LASTEXITCODE

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$DepsPath = Join-Path $ProjectRoot ".deps"
$PytestPath = Join-Path $DepsPath "bin\pytest.exe"

if (-not (Test-Path $PytestPath)) {
    Write-Error "pytest.exe not found. Install dependencies into shipping4/.deps first."
    exit 1
}

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$DepsPath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $DepsPath
}

& $PytestPath @args
exit $LASTEXITCODE

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$atlasPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $atlasPython)) {
    throw 'Create .venv and install the project first. See docs/quickstart.md.'
}
& $atlasPython -m streamlit run app.py --server.address 127.0.0.1 --server.headless true

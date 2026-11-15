# One-command idea test. Examples (from the Tradzen folder):
#   .\idea.ps1 expr --call "ms.bias == 1 and green and close > e9" --put "ms.bias == -1 and red and close < e9"
#   .\idea.ps1 spec examples\tr4-trend-and-shift-down.json
#   .\idea.ps1 model          # LightGBM / CNN / GRU / Transformer bake-off on the next candle
#   .\idea.ps1 ledger         # every idea tried so far
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$root\backend\.venv\Scripts\python.exe" "$root\backend\tools\idea_test.py" @args
exit $LASTEXITCODE

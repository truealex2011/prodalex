$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$py='.\.venv\Scripts\python.exe'
$bin='C:\avcpginstall\bin'
$pgdata='C:\AVCUtf8b'
$dburi='postgresql://autochoice:autochoice@127.0.0.1:55433/autochoice'

# 1. PostgreSQL on a fixed port
if (-not (& "$bin\pg_isready.exe" -h 127.0.0.1 -p 55433 -q 2>$null)) {
  & "$bin\pg_ctl.exe" -D $pgdata -l "$pgdata\pg.log" -o "-h 127.0.0.1 -p 55433" -w start
}

# 2. Environment for this shell
$env:DATABASE_URL=$dburi
$env:PYTHONUTF8='1'
$env:MODEL_INDEX_PREWARM='0'
$env:PARSER_URL='http://localhost:3100'
$env:PARSER_DIR=(Join-Path $PSScriptRoot '..')

# 3. Parser sidecar on 3100 (starts only if port is free)
if (-not (Get-NetTCPConnection -LocalPort 3100 -State Listen -ErrorAction SilentlyContinue)) {
  $env:PORT='3100'
  Start-Process node -ArgumentList 'server.js' -WorkingDirectory (Join-Path $PSScriptRoot 'parser') -WindowStyle Hidden
  $env:PORT=''
}

# 4. Web app on 8000 (starts only if port is free)
if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
  Start-Process -FilePath $py -ArgumentList '-m flask --app wsgi run --host 127.0.0.1 --port 8000' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}

Start-Sleep -Seconds 4
'AutoChoice:  http://127.0.0.1:8000/'
'Parser:      http://localhost:3100/health'
'PostgreSQL:  127.0.0.1:55433'

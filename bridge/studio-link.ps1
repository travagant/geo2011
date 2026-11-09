<#
  Start the Arena <-> AstraForge link with one double-click.

  Runs bridge\studio_link.py on this PC (localhost only), opens a temporary
  cloudflared tunnel to it, and prints the URL + token that the Arena session
  needs.  Nothing is written outside this repository except cloudflared.exe
  (into bridge\.link\) and the tunnel's own temp files.
#>

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo   = Split-Path -Parent $here
$link   = Join-Path $here '.link'
$port   = 3211
New-Item -ItemType Directory -Force -Path $link | Out-Null

$studio = 'D:\AstraForge'
$cfgPath = Join-Path $here 'config.json'
if (Test-Path $cfgPath) {
    try { $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch { $cfg = $null }
    if ($cfg -and $cfg.studio) { $studio = $cfg.studio }
}

function Say($text) { Write-Host $text }

Say ''
Say '=== AstraForge <-> Arena: studio link ==='
Say ''

# ---------------------------------------------------------------- python
$candidates = New-Object System.Collections.ArrayList
[void]$candidates.Add(@{ exe = 'py';     pre = @('-3') })
[void]$candidates.Add(@{ exe = 'python'; pre = @() })
[void]$candidates.Add(@{ exe = (Join-Path $studio 'runtime\comfy\.venv\Scripts\python.exe'); pre = @() })
[void]$candidates.Add(@{ exe = (Join-Path $studio 'runtime\python\python.exe'); pre = @() })
Get-ChildItem -Path (Join-Path $studio 'runtime') -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 5 | ForEach-Object { [void]$candidates.Add(@{ exe = $_.FullName; pre = @() }) }

$pyExe = $null
$pyPre = @()
foreach ($cand in $candidates) {
    if ([string]::IsNullOrWhiteSpace($cand.exe)) { continue }
    if ($cand.exe -match '\\' -and -not (Test-Path $cand.exe)) { continue }
    $pre = @($cand.pre)
    try {
        $null = & $cand.exe @pre -c "import sys" 2>&1
        if ($LASTEXITCODE -eq 0) { $pyExe = $cand.exe; $pyPre = $cand.pre; break }
    } catch { }
}
if (-not $pyExe) {
    Say 'НЕ НАЙДЕН Python. Поставь Python 3.10+ (python.org, галочка "Add to PATH")'
    Say 'или запусти установщик студии: он ставит свой Python для ComfyUI.'
    Read-Host 'Enter — закрыть'
    exit 1
}
Say "python : $pyExe"
Say "репо   : $repo"
Say "студия : $studio"

if (-not (Test-Path $cfgPath)) {
    Say 'config.json нет — создаю из примера (потом впиши donor / mods).'
    & $pyExe @pyPre (Join-Path $here 'astra_d3.py') config
}

# ---------------------------------------------------------------- server
$serverOut = Join-Path $link 'server.log'
$serverErr = Join-Path $link 'server.err.log'

$serverArgs = @()
foreach ($a in $pyPre) { $serverArgs += $a }
$scriptPath = Join-Path $here 'studio_link.py'
$serverArgs += @("`"$scriptPath`"", 'serve', '--port', "$port")   # quoted: paths may contain spaces

$server = Start-Process -FilePath $pyExe -ArgumentList $serverArgs -WorkingDirectory $repo `
                        -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr `
                        -WindowStyle Hidden -PassThru

$up = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 300
    try {
        $client = New-Object Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', $port)
        $client.Close()
        $up = $true
        break
    } catch { }
    if ($server.HasExited) { break }
}
if (-not $up) {
    Say 'Сервер не поднялся. Вот его лог:'
    if (Test-Path $serverErr) { Get-Content $serverErr -Tail 30 }
    if (Test-Path $serverOut) { Get-Content $serverOut -Tail 30 }
    Read-Host 'Enter — закрыть'
    exit 1
}

$token = (Get-Content (Join-Path $link 'token.txt') -Raw).Trim()
Say "сервер : http://127.0.0.1:$port  (только этот ПК)"

# ---------------------------------------------------------------- tunnel
$cloudflared = $null
foreach ($cand in @(
        (Join-Path $link 'cloudflared.exe'),
        (Join-Path $studio 'runtime\cloudflared\cloudflared.exe'),
        (Join-Path $studio 'tools\cloudflared.exe'))) {
    if (Test-Path $cand) { $cloudflared = $cand; break }
}
if (-not $cloudflared) {
    $onPath = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($onPath) { $cloudflared = $onPath.Source }
}
if (-not $cloudflared) {
    $cloudflared = Join-Path $link 'cloudflared.exe'
    Say 'Скачиваю cloudflared (~35 МБ, один раз)…'
    try {
        Invoke-WebRequest -UseBasicParsing -OutFile $cloudflared `
            -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
    } catch {
        Say "Не скачалось: $($_.Exception.Message)"
        Say 'Скачай вручную и положи рядом: bridge\.link\cloudflared.exe'
        Read-Host 'Enter — закрыть'
        exit 1
    }
}
Say "туннель : $cloudflared"

$tunnelLog = Join-Path $link 'tunnel.log'
if (Test-Path $tunnelLog) { Remove-Item $tunnelLog -Force }
$tunnel = Start-Process -FilePath $cloudflared `
    -ArgumentList @('tunnel', '--url', "http://127.0.0.1:$port", '--no-autoupdate',
                    '--logfile', $tunnelLog, '--loglevel', 'info') `
    -WindowStyle Hidden -PassThru

$url = $null
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $tunnelLog) {
        $hit = Select-String -Path $tunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' `
                             -AllMatches -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) {
            $url = $hit.Matches[0].Value
            break
        }
    }
    if ($tunnel.HasExited) { break }
}
if (-not $url) {
    Say 'Туннель не отдал адрес. Лог:'
    if (Test-Path $tunnelLog) { Get-Content $tunnelLog -Tail 25 }
    Read-Host 'Enter — закрыть'
    exit 1
}

$line = "link $url $token"
try { Set-Clipboard -Value $line } catch { }

Say ''
Say '============================================================='
Say ' Адрес : ' + $url
Say ' Токен : ' + $token
Say ''
Say ' Скопировано в буфер. Вставь это в чат одной строкой:'
Say '   ' + $line
Say ''
Say ' Пока это окно открыто — Arena видит студию.'
Say ' Локально можно проверить: ' + "http://127.0.0.1:$port/cmd?token=$token&op=hello"
Say '============================================================='
Say ''
Read-Host 'Enter — остановить туннель и сервер'

try { Stop-Process -Id $tunnel.Id -Force } catch { }
try { Stop-Process -Id $server.Id -Force } catch { }
Say 'Остановлено.'

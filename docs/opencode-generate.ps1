# Собирает opencode.json из объектов PowerShell, а не из текста.
# Запуск:  powershell -ExecutionPolicy Bypass -File docs\opencode-generate.ps1
# Результат: %USERPROFILE%\.config\opencode\opencode.json
# Ключи подставить после создания: COARENA_KEY_HERE и ZRO_KEY_HERE.

$ErrorActionPreference = 'Stop'

$p = Join-Path $env:USERPROFILE '.config\opencode\opencode.json'
New-Item -ItemType Directory -Force (Split-Path $p) | Out-Null

# URL собираются по частям: в исходнике нет строки с двойным слешем после схемы,
# поэтому автолинковка в чатах её не тронет.
$u = 'https:' + '//'

# Подчёркивание берём кодом символа: имя tool_output иначе рискует приехать
# как tool_output с обратным слешем, и JSON перестанет парситься.
$us = [char]95

$toolOutput = 'tool' + $us + 'output'
$maxLines = 'max' + $us + 'lines'
$maxBytes = 'max' + $us + 'bytes'

$cfg = [ordered]@{
  '$schema' = $u + 'opencode.ai/config.json'
  provider  = [ordered]@{
    coarena  = [ordered]@{
      npm     = '@ai-sdk/openai-compatible'
      name    = 'Coarena'
      options = [ordered]@{
        baseURL = $u + 'coarena.ai/api/v1'
        apiKey  = 'COARENA_KEY_HERE'
      }
      models  = [ordered]@{
        'deepseek-v4-1-flash-1017' = [ordered]@{
          name  = 'DeepSeek V4.1 Flash (Coarena)'
          limit = [ordered]@{ context = 131072; output = 16384 }
        }
      }
    }
    zro      = [ordered]@{
      npm     = '@ai-sdk/openai-compatible'
      name    = 'Zro'
      options = [ordered]@{
        baseURL = $u + 'zro.moonmath.ai/v1'
        apiKey  = 'ZRO_KEY_HERE'
      }
      models  = [ordered]@{
        'deepseek-v4.1-flash' = [ordered]@{
          name  = 'DeepSeek V4.1 Flash (Zro)'
          limit = [ordered]@{ context = 131072; output = 16384 }
        }
      }
    }
    opencode = [ordered]@{
      name   = 'OpenCode Zen'
      models = [ordered]@{
        'deepseek-v4.1-flash-free' = [ordered]@{
          name  = 'DeepSeek V4.1 Flash Free (Zen)'
          limit = [ordered]@{ context = 131072; output = 16384 }
        }
      }
    }
  }
  model      = 'coarena/deepseek-v4-1-flash-1017'
  compaction = [ordered]@{
    auto       = $true
    background = $false
    keep       = [ordered]@{ tokens = 8000 }
    buffer     = '20%'
  }
  agents     = [ordered]@{
    build      = [ordered]@{ steps = 120 }
    compaction = [ordered]@{ model = 'opencode/deepseek-v4.1-flash-free' }
  }
}

$cfg[$toolOutput] = [ordered]@{ $maxLines = 600; $maxBytes = 16000 }

$json = $cfg | ConvertTo-Json -Depth 10
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($p, $json, $utf8)

# Проверка с самопочинкой: если файл всё же побился при передаче, чистим и проверяем снова.
function Test-Json([string]$text) {
  try { $null = $text | ConvertFrom-Json; return $true } catch { return $false }
}

$raw = [System.IO.File]::ReadAllText($p)
if (-not (Test-Json $raw)) {
  $fixed = [regex]::Replace($raw, '\]\([^)]*\)', '')
  $fixed = [regex]::Replace($fixed, '\[(?=\s*"?https?://)', '')
  $fixed = $fixed.Replace('\_', '_')
  [System.IO.File]::WriteAllText($p, $fixed, $utf8)
  $raw = $fixed
}

if (Test-Json $raw) {
  $obj = $raw | ConvertFrom-Json
  $names = $obj.provider.PSObject.Properties.Name -join ', '
  Write-Host "JSON ок. Провайдеры: $names"
  Write-Host "Файл: $p"
  Write-Host 'Дальше: заменить COARENA_KEY_HERE и ZRO_KEY_HERE на настоящие ключи.'
} else {
  Write-Host 'Файл битый, автопочинка не помогла. Не запускай opencode - сначала пришли файл целиком.'
  exit 1
}

# Установка локальной нейросети для сайта: Qwen2.5-1.5B-Instruct (GGUF, Q4_K_M) + llama.cpp.
# Всё ставится в %USERPROFILE%\.cache\autochoice. Размер: модель ≈ 1,1 ГБ, llama.cpp ≈ 25 МБ.
# Файлы проверяются по SHA-256 (те же хеши, что в проекте «парсинк»). Запуск:  powershell -File tools\setup_llm.ps1
$ErrorActionPreference = 'Stop'
$root = Join-Path $HOME '.cache\autochoice'
$models = Join-Path $root 'models'
$llama = Join-Path $root 'llama'
New-Item -ItemType Directory -Force -Path $models, $llama | Out-Null

function Get-Verified($urls, $path, $sha) {
    if ((Test-Path $path) -and ((Get-FileHash $path -Algorithm SHA256).Hash -eq $sha)) { Write-Host "уже есть: $path"; return }
    foreach ($url in $urls) {
        Write-Host "скачиваю $url"
        curl.exe --fail --location --silent --show-error --retry 2 --max-time 3600 -o "$path.part" $url
        if ($LASTEXITCODE -eq 0 -and (Get-FileHash "$path.part" -Algorithm SHA256).Hash -eq $sha) { Move-Item -Force "$path.part" $path; return }
        Remove-Item "$path.part" -Force -ErrorAction SilentlyContinue
    }
    throw "Не удалось скачать $path или не совпала контрольная сумма"
}

Get-Verified @(
    'https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf?download=true',
    'https://modelscope.cn/models/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/master/qwen2.5-1.5b-instruct-q4_k_m.gguf'
) (Join-Path $models 'planner.gguf') '6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e'

$zip = Join-Path $env:TEMP 'autochoice-llama-b12576.zip'
Get-Verified @('https://github.com/ggml-org/llama.cpp/releases/download/b12576/llama-b12576-bin-win-cpu-x64.zip') $zip 'c8bda771161ab75e259568ea178122b7567fb2d51c1a5ac755d308897e9b1c20'
Expand-Archive $zip -DestinationPath $llama -Force
Remove-Item $zip -Force
if (-not (Test-Path (Join-Path $llama 'llama-completion.exe'))) { throw 'llama-completion.exe не найден после распаковки' }
Write-Host 'Готово. Перезапустите сайт: нейросеть подключится сама.'

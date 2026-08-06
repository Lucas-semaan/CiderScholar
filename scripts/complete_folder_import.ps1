[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceDirectory,

    [Parameter(Mandatory = $true)]
    [string]$ConvertedDirectory,

    [int]$WaitForProcessId = 0
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$pythonExecutable = (Resolve-Path -LiteralPath (
    Join-Path $projectRoot ".venv\Scripts\python.exe"
)).Path
$sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path
$convertedRoot = [System.IO.Path]::GetFullPath($ConvertedDirectory)
$startedAt = [DateTime]::UtcNow
$steps = [System.Collections.Generic.List[object]]::new()

function Invoke-ImportStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Operation
    )

    $stepStartedAt = [DateTime]::UtcNow
    Write-Host "Étape : $Name"
    & $Operation
    $exitCode = $LASTEXITCODE
    $steps.Add([pscustomobject]@{
        name = $Name
        exit_code = $exitCode
        started_at = $stepStartedAt.ToString("o")
        finished_at = [DateTime]::UtcNow.ToString("o")
    })
    Write-Host "  code=$exitCode"
}

if ($WaitForProcessId -gt 0) {
    $existing = Get-Process -Id $WaitForProcessId -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Write-Host "Attente du processus PDF actif : $WaitForProcessId"
        Wait-Process -Id $WaitForProcessId
    }
}

Push-Location $projectRoot
try {
    Invoke-ImportStep -Name "pdf_retry" -Operation {
        & $pythonExecutable -u -m scripts.ingest_folder `
            $sourceRoot `
            --recursive `
            --ocr `
            --skip-known `
            --wait-for-memory
    }

    Invoke-ImportStep -Name "office_conversion" -Operation {
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File (Join-Path $PSScriptRoot "convert_text_documents.ps1") `
            -SourceDirectory $sourceRoot `
            -OutputDirectory $convertedRoot `
            -Recursive
    }

    Invoke-ImportStep -Name "converted_pdf_ingestion" -Operation {
        & $pythonExecutable -u -m scripts.ingest_folder `
            $convertedRoot `
            --recursive `
            --ocr `
            --skip-known `
            --wait-for-memory
    }

    Invoke-ImportStep -Name "corpus_vector_index" -Operation {
        & $pythonExecutable -u -m scripts.rebuild_index --retry-failed
    }
}
finally {
    Pop-Location
}

$reportDirectory = Join-Path $projectRoot "data\exports"
[System.IO.Directory]::CreateDirectory($reportDirectory) | Out-Null
$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$reportPath = Join-Path $reportDirectory "complete-folder-import-$timestamp.json"
[pscustomobject]@{
    source_directory = $sourceRoot
    converted_directory = $convertedRoot
    corpus = "common"
    started_at = $startedAt.ToString("o")
    finished_at = [DateTime]::UtcNow.ToString("o")
    steps = $steps
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host "Rapport : $reportPath"
if (@($steps | Where-Object { $_.exit_code -ne 0 }).Count -gt 0) {
    exit 1
}

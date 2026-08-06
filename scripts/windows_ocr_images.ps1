param(
    [Parameter(Mandatory = $true)]
    [string]$InputDirectory,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [string]$Language = "fr-FR"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.FileAccessMode, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

function Wait-WinRtOperation {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Operation,
        [Parameter(Mandatory = $true)]
        [Type]$ResultType
    )
    $asTaskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq "AsTask" -and $_.IsGenericMethod -and
            $_.GetParameters().Count -eq 1
        } |
        Select-Object -First 1
    $task = $asTaskMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

$languageObject = [Windows.Globalization.Language]::new($Language)
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languageObject)
if ($null -eq $engine) {
    throw "Windows OCR language is unavailable: $Language"
}

$results = @()
Get-ChildItem -LiteralPath $InputDirectory -Filter "*.png" -File |
    Sort-Object Name |
    ForEach-Object {
        $storageFile = Wait-WinRtOperation `
            ([Windows.Storage.StorageFile]::GetFileFromPathAsync($_.FullName)) `
            ([Windows.Storage.StorageFile])
        $stream = Wait-WinRtOperation `
            ($storageFile.OpenAsync([Windows.Storage.FileAccessMode]::Read)) `
            ([Windows.Storage.Streams.IRandomAccessStream])
        try {
            $decoder = Wait-WinRtOperation `
                ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) `
                ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Wait-WinRtOperation `
                ($decoder.GetSoftwareBitmapAsync()) `
                ([Windows.Graphics.Imaging.SoftwareBitmap])
            try {
                $ocrResult = Wait-WinRtOperation `
                    ($engine.RecognizeAsync($bitmap)) `
                    ([Windows.Media.Ocr.OcrResult])
                $results += [PSCustomObject]@{
                    file_name = $_.Name
                    text = $ocrResult.Text
                    language = $Language
                    line_count = @($ocrResult.Lines).Count
                    word_count = @($ocrResult.Lines | ForEach-Object { $_.Words }).Count
                }
            }
            finally {
                if ($null -ne $bitmap) { $bitmap.Dispose() }
            }
        }
        finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
    }

ConvertTo-Json -InputObject @($results) -Depth 3 |
    Set-Content -LiteralPath $OutputPath -Encoding UTF8

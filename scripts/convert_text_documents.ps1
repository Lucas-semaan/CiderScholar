[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceDirectory,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [switch]$Recursive,

    [switch]$StopOnError
)

$ErrorActionPreference = "Stop"
$wordExtensions = @(".doc", ".docx", ".htm", ".html", ".mht", ".odt", ".rtf", ".txt")
$powerPointExtensions = @(".pps", ".ppt", ".pptx")
$excelExtensions = @(".csv", ".xls", ".xlsx")
$supportedExtensions = $wordExtensions + $powerPointExtensions + $excelExtensions
$sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$pythonExecutable = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe')).Path
[System.IO.Directory]::CreateDirectory($outputRoot) | Out-Null

function Get-RelativePathHash {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($RelativePath.ToLowerInvariant())
        $digest = $algorithm.ComputeHash($bytes)
        return (-join ($digest | ForEach-Object { $_.ToString("x2") })).Substring(0, 16)
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-DestinationPath {
    param([Parameter(Mandatory = $true)][System.IO.FileInfo]$SourceFile)

    $rootWithSeparator = $sourceRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $SourceFile.FullName.StartsWith(
        $rootWithSeparator,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Source file escaped the requested source directory"
    }
    $relativePath = $SourceFile.FullName.Substring($rootWithSeparator.Length)
    $prefix = Get-RelativePathHash -RelativePath $relativePath
    $safeName = [regex]::Replace($SourceFile.Name, '[^\p{L}\p{Nd}._ -]+', '_').Trim(' ', '.')
    if ($safeName.Length -gt 140) {
        $safeName = $safeName.Substring(0, 140).TrimEnd(' ', '.')
    }
    return Join-Path $outputRoot "$prefix-$safeName.pdf"
}

function Release-ComObject {
    param([object]$Value)

    if ($null -ne $Value -and [System.Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Get-PresentationText {
    param([Parameter(Mandatory = $true)][object]$Presentation)

    $parts = [System.Collections.Generic.List[string]]::new()
    foreach ($slide in $Presentation.Slides) {
        $parts.Add("Diapositive $($slide.SlideIndex)")
        foreach ($shape in $slide.Shapes) {
            try {
                if ($shape.HasTextFrame -and $shape.TextFrame.HasText) {
                    $text = [string]$shape.TextFrame.TextRange.Text
                    if (-not [string]::IsNullOrWhiteSpace($text)) {
                        $parts.Add($text)
                    }
                }
            }
            catch { }
            finally {
                Release-ComObject -Value $shape
            }
        }
        Release-ComObject -Value $slide
    }
    return [string]::Join([Environment]::NewLine, $parts)
}

function Get-WorkbookText {
    param([Parameter(Mandatory = $true)][object]$Workbook)

    $parts = [System.Collections.Generic.List[string]]::new()
    foreach ($worksheet in $Workbook.Worksheets) {
        $parts.Add("Feuille $($worksheet.Name)")
        $range = $null
        try {
            $range = $worksheet.UsedRange
            $values = $range.Value2
            if ($values -is [System.Array]) {
                for ($row = $values.GetLowerBound(0); $row -le $values.GetUpperBound(0); $row++) {
                    $cells = [System.Collections.Generic.List[string]]::new()
                    for (
                        $column = $values.GetLowerBound(1);
                        $column -le $values.GetUpperBound(1);
                        $column++
                    ) {
                        $value = [string]$values.GetValue($row, $column)
                        if (-not [string]::IsNullOrWhiteSpace($value)) {
                            $cells.Add($value)
                        }
                    }
                    if ($cells.Count -gt 0) {
                        $parts.Add([string]::Join(" | ", $cells))
                    }
                }
            }
            elseif (-not [string]::IsNullOrWhiteSpace([string]$values)) {
                $parts.Add([string]$values)
            }
        }
        finally {
            Release-ComObject -Value $range
            Release-ComObject -Value $worksheet
        }
    }
    return [string]::Join([Environment]::NewLine, $parts)
}

$searchParameters = @{
    LiteralPath = $sourceRoot
    File = $true
    Force = $true
}
if ($Recursive) {
    $searchParameters.Recurse = $true
}
$sourceFiles = @(
    Get-ChildItem @searchParameters |
        Where-Object { $supportedExtensions -contains $_.Extension.ToLowerInvariant() } |
        Sort-Object FullName
)

$word = $null
$powerPoint = $null
$excel = $null
$reports = [System.Collections.Generic.List[object]]::new()
$stopRequested = $false

try {
    for ($index = 0; $index -lt $sourceFiles.Count; $index++) {
        $sourceFile = $sourceFiles[$index]
        $extension = $sourceFile.Extension.ToLowerInvariant()
        $destination = Get-DestinationPath -SourceFile $sourceFile
        Write-Host "[$($index + 1)/$($sourceFiles.Count)] $($sourceFile.FullName)"

        if (
            (Test-Path -LiteralPath $destination -PathType Leaf) -and
            (Get-Item -LiteralPath $destination).Length -gt 0 -and
            (Get-Item -LiteralPath $destination).LastWriteTimeUtc -ge $sourceFile.LastWriteTimeUtc
        ) {
            $reports.Add([pscustomobject]@{
                source = $sourceFile.FullName
                destination = $destination
                status = "cached"
                error_type = $null
                error_message = $null
            })
            Write-Host "  état=cached"
            continue
        }

        $temporary = Join-Path $outputRoot ".$([guid]::NewGuid().ToString('N')).txt"
        $document = $null
        try {
            $text = ""
            if ($wordExtensions -contains $extension) {
                if ($null -eq $word) {
                    $word = New-Object -ComObject Word.Application
                    $word.Visible = $false
                    $word.DisplayAlerts = 0
                    $word.AutomationSecurity = 3
                    $word.Options.SaveNormalPrompt = $false
                    $word.Options.UpdateLinksAtOpen = $false
                }
                $document = $word.Documents.Open($sourceFile.FullName, $false, $true)
                $text = [string]$document.Content.Text
                $document.Close(0)
                Release-ComObject -Value $document
                $document = $null
            }
            elseif ($powerPointExtensions -contains $extension) {
                if ($null -eq $powerPoint) {
                    $powerPoint = New-Object -ComObject PowerPoint.Application
                    $powerPoint.AutomationSecurity = 3
                }
                $document = $powerPoint.Presentations.Open(
                    $sourceFile.FullName,
                    $true,
                    $true,
                    $false
                )
                $text = Get-PresentationText -Presentation $document
                $document.Close()
                Release-ComObject -Value $document
                $document = $null
            }
            else {
                if ($null -eq $excel) {
                    $excel = New-Object -ComObject Excel.Application
                    $excel.Visible = $false
                    $excel.DisplayAlerts = $false
                    $excel.AutomationSecurity = 3
                    $excel.AskToUpdateLinks = $false
                }
                $document = $excel.Workbooks.Open($sourceFile.FullName, 0, $true)
                $text = Get-WorkbookText -Workbook $document
                $document.Close($false)
                Release-ComObject -Value $document
                $document = $null
            }

            if ([string]::IsNullOrWhiteSpace($text)) {
                throw "Office did not expose any text"
            }
            [System.IO.File]::WriteAllText($temporary, $text, [System.Text.UTF8Encoding]::new($false))
            & $pythonExecutable -m scripts.render_text_pdf `
                $temporary `
                $destination `
                --title $sourceFile.Name `
                --source-path $sourceFile.FullName
            if ($LASTEXITCODE -ne 0) {
                throw "Local text-to-PDF renderer failed with exit code $LASTEXITCODE"
            }
            Remove-Item -LiteralPath $temporary -Force
            $reports.Add([pscustomobject]@{
                source = $sourceFile.FullName
                destination = $destination
                status = "converted"
                error_type = $null
                error_message = $null
            })
            Write-Host "  état=converted"
        }
        catch {
            if ($null -ne $document) {
                try { $document.Close($false) } catch { }
                Release-ComObject -Value $document
            }
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
            $reports.Add([pscustomobject]@{
                source = $sourceFile.FullName
                destination = $destination
                status = "failed"
                error_type = $_.Exception.GetType().Name
                error_message = $_.Exception.Message.Substring(0, [Math]::Min(1000, $_.Exception.Message.Length))
            })
            Write-Warning "  état=failed type=$($_.Exception.GetType().Name)"
            if ($StopOnError) {
                $stopRequested = $true
                break
            }
        }
    }
}
finally {
    if ($null -ne $word) {
        try { $word.Quit() } catch { }
        Release-ComObject -Value $word
    }
    if ($null -ne $powerPoint) {
        try { $powerPoint.Quit() } catch { }
        Release-ComObject -Value $powerPoint
    }
    if ($null -ne $excel) {
        try { $excel.Quit() } catch { }
        Release-ComObject -Value $excel
    }
    [gc]::Collect()
    [gc]::WaitForPendingFinalizers()
}

$statusCounts = @{}
foreach ($group in ($reports | Group-Object status)) {
    $statusCounts[$group.Name] = $group.Count
}
$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$reportPath = Join-Path $outputRoot "conversion-$timestamp.json"
[pscustomobject]@{
    created_at = [DateTime]::UtcNow.ToString("o")
    source_directory = $sourceRoot
    output_directory = $outputRoot
    recursive = [bool]$Recursive
    selected_file_count = $sourceFiles.Count
    status_counts = $statusCounts
    stopped_on_error = $stopRequested
    reports = $reports
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host "Rapport : $reportPath"
if ($statusCounts.ContainsKey("failed")) {
    exit 1
}

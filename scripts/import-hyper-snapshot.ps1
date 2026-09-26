param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,
    [switch]$Replace
)

$ErrorActionPreference = 'Stop'

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $projectRoot 'runtime'
$targetRoot = Join-Path $runtimeRoot 'hyper-snapshot'
$runtimePrefix = [System.IO.Path]::GetFullPath($runtimeRoot).TrimEnd('\') + '\'
function Assert-RuntimeChild([string]$candidate) {
    $resolved = [System.IO.Path]::GetFullPath($candidate)
    if (-not $resolved.StartsWith($runtimePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected snapshot path outside project runtime: $resolved"
    }
}
Assert-RuntimeChild $targetRoot
$resolvedArchive = (Resolve-Path -LiteralPath $ArchivePath).Path
if (-not (Test-Path -LiteralPath $resolvedArchive -PathType Leaf)) {
    throw "Archive not found: $resolvedArchive"
}
if ((Test-Path -LiteralPath $targetRoot) -and -not $Replace) {
    throw "Snapshot already exists at $targetRoot; pass -Replace to preserve it as a timestamped backup"
}
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

$stagingRoot = Join-Path $runtimeRoot ("hyper-snapshot-import-" + [guid]::NewGuid().ToString('N'))
Assert-RuntimeChild $stagingRoot
New-Item -ItemType Directory -Path $stagingRoot | Out-Null
$zip = $null
try {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($resolvedArchive)
    foreach ($scope in @('ZN', 'GW')) {
        $scopeFolder = Join-Path $stagingRoot $scope
        New-Item -ItemType Directory -Path $scopeFolder | Out-Null
        foreach ($file in @('data.json', 'metadata.json', 'sources_nodes.json', 'sources_edges.json')) {
            # Only extract these exact archive members. Never trust a ZIP path
            # as a destination, and never import the bundled .env or .venv.
            $entry = $zip.GetEntry("Hyper-Extract/$scope/$file")
            if ($null -eq $entry -or $entry.Length -gt 30000000) {
                throw "Missing or oversized Hyper snapshot member: $scope/$file"
            }
            $destination = Join-Path $scopeFolder $file
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination)
        }
        $graph = Get-Content -LiteralPath (Join-Path $scopeFolder 'data.json') -Raw -Encoding UTF8 |
            ConvertFrom-Json -Depth 100
        if (@($graph.nodes).Count -eq 0 -or @($graph.edges).Count -eq 0) {
            throw "Hyper snapshot $scope has no nodes or edges"
        }
    }
} catch {
    Write-Warning "Import failed; staged files were left at $stagingRoot for inspection"
    throw
} finally {
    if ($zip) { $zip.Dispose() }
}

if (Test-Path -LiteralPath $targetRoot) {
    $backup = Join-Path $runtimeRoot ("hyper-snapshot-backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    Assert-RuntimeChild $backup
    Move-Item -LiteralPath $targetRoot -Destination $backup
    Write-Host "Previous snapshot backed up to $backup"
}
Move-Item -LiteralPath $stagingRoot -Destination $targetRoot
Write-Host "Imported Hyper-Extract ZN/GW snapshots to $targetRoot"

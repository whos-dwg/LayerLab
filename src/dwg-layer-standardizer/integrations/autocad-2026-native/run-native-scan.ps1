param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [Parameter(Mandatory = $true)]
    [string]$Standard,

    [Parameter(Mandatory = $true)]
    [string]$OutFolder
)

$ErrorActionPreference = 'Stop'

function Get-Sha256([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try { return -join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString('X2') }) }
        finally { $sha.Dispose() }
    }
    finally { $stream.Dispose() }
}

$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$standardPath = (Resolve-Path -LiteralPath $Standard).Path
$projectRoot = $PSScriptRoot
$pluginPath = Join-Path $projectRoot 'bin\Release\net10.0-windows\LayerLab.NativeLayerExport.dll'
$projectPath = Join-Path $projectRoot 'LayerLab.NativeLayerExport.csproj'
$coreConsole = 'C:\Program Files\Autodesk\AutoCAD 2026\accoreconsole.exe'

if (-not (Test-Path -LiteralPath $coreConsole)) {
    throw "AutoCAD 2026 Core Console not found: $coreConsole"
}

if (-not (Test-Path -LiteralPath $pluginPath)) {
    dotnet build $projectPath -c Release --nologo
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pluginPath)) {
        throw 'Native scanner build failed.'
    }
}

$outPath = [System.IO.Path]::GetFullPath($OutFolder)
if (Test-Path -LiteralPath $outPath) {
    throw "Output folder already exists: $outPath"
}
[System.IO.Directory]::CreateDirectory($outPath) | Out-Null

$inventoryPath = Join-Path $outPath (([System.IO.Path]::GetFileNameWithoutExtension($sourcePath)) + '.native.inventory.json')
$suggestionsPath = Join-Path $outPath (([System.IO.Path]::GetFileNameWithoutExtension($sourcePath)) + '.native.suggestions.json')
$scriptPath = Join-Path $outPath 'scan.generated.scr'
$pluginForScript = $pluginPath.Replace('\', '/')
$pluginDirectoryForScript = (Split-Path -Parent $pluginPath).Replace('\','/')
$trustBeforeForScript = (Join-Path $outPath 'trustedpaths.before.txt').Replace('\','/')
$trustedLoad = '(progn (setq recovery-old-trust (getvar "TRUSTEDPATHS")) (setq recovery-f (open "' + $trustBeforeForScript + '" "w")) (write-line recovery-old-trust recovery-f) (close recovery-f) (setvar "TRUSTEDPATHS" (if (= recovery-old-trust "") "' + $pluginDirectoryForScript + '" (strcat recovery-old-trust ";' + $pluginDirectoryForScript + '"))) (setq recovery-result (vl-catch-all-apply (function (lambda () (command "_.NETLOAD" "' + $pluginForScript + '"))) nil)) (setvar "TRUSTEDPATHS" recovery-old-trust) (princ (strcat "\nRECOVERY_TRUST_RESTORED=" (if (equal recovery-old-trust (getvar "TRUSTEDPATHS")) "YES" "NO") "\n")) (if (vl-catch-all-error-p recovery-result) (princ (vl-catch-all-error-message recovery-result))) (princ))'
$scriptLines = @(
    $trustedLoad,
    'LAYERLABSCANNATIVE',
    '_.QUIT'
)
[System.IO.File]::WriteAllLines($scriptPath, $scriptLines, [System.Text.UTF8Encoding]::new($false))

$before = Get-Sha256 $sourcePath
$env:LAYERLAB_SOURCE_PATH = $sourcePath
$env:LAYERLAB_INVENTORY_OUT = $inventoryPath
$env:LAYERLAB_STANDARD_PATH = $standardPath
$env:LAYERLAB_SUGGESTIONS_OUT = $suggestionsPath

$arguments = @('/i', ('"' + $sourcePath + '"'), '/s', ('"' + $scriptPath + '"'), '/l', 'cht')
$stdout = Join-Path $outPath 'core.stdout.log'
$stderr = Join-Path $outPath 'core.stderr.log'
$process = Start-Process -FilePath $coreConsole -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$retainedHandle = $process.Handle
[ordered]@{pid=$process.Id;started_utc=[DateTime]::UtcNow.ToString('o');executable=$coreConsole;arguments=$arguments;timeout_seconds=180} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outPath 'process.json') -Encoding utf8
if (-not $process.WaitForExit(180000)) {
    $process.Refresh()
    if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
    throw 'Owned native scan process exceeded 180 seconds. See core.stdout.log and core.stderr.log.'
}
$process.Refresh()
$exitCode = $process.ExitCode
$after = Get-Sha256 $sourcePath

if ($exitCode -ne 0) {
    throw "AutoCAD native scan failed with exit code $exitCode."
}
if ($before -ne $after) {
    throw 'Source SHA-256 changed during read-only scan.'
}
if (-not (Test-Path -LiteralPath $inventoryPath) -or -not (Test-Path -LiteralPath $suggestionsPath)) {
    throw 'Native scan did not produce both required JSON outputs.'
}

[PSCustomObject]@{
    Status = 'PASS'
    SourceUnchanged = $true
    SourceSha256 = $after
    Inventory = $inventoryPath
    Suggestions = $suggestionsPath
    AuditScript = $scriptPath
}

param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [Parameter(Mandatory = $true)]
    [string]$Mapping,

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
$mappingPath = (Resolve-Path -LiteralPath $Mapping).Path
$standardPath = (Resolve-Path -LiteralPath $Standard).Path
$projectRoot = $PSScriptRoot
$pluginPath = Join-Path $projectRoot 'bin\Release\net10.0-windows\LayerLab.NativeLayerExport.dll'
$projectPath = Join-Path $projectRoot 'LayerLab.NativeLayerExport.csproj'
$coreConsole = 'C:\Program Files\Autodesk\AutoCAD 2026\accoreconsole.exe'

function Invoke-CoreConsole([string]$Drawing, [string]$Script, [string]$SuccessMarker, [string]$TraceFile) {
    $arguments = @('/i', ('"' + $Drawing + '"'), '/s', ('"' + $Script + '"'), '/l', 'cht')
    $process = Start-Process -FilePath $coreConsole -ArgumentList $arguments -WindowStyle Hidden -PassThru -WorkingDirectory (Split-Path -Parent $Script) -RedirectStandardOutput ($Script + '.stdout.log') -RedirectStandardError ($Script + '.stderr.log')
    $retainedHandle = $process.Handle
    $confirmed = $false
    for ($attempt = 0; $attempt -lt 1200; $attempt++) {
        $process.Refresh()
        if (Test-Path -LiteralPath $TraceFile) {
            $confirmed = [bool](Select-String -LiteralPath $TraceFile -SimpleMatch $SuccessMarker -Quiet)
            if ($confirmed) { break }
        }
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 250
    }
    $process.Refresh()
    if (-not $confirmed) {
        $code = if ($process.HasExited) { $process.ExitCode } else { 'timeout' }
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        throw "AutoCAD stage did not emit success marker '$SuccessMarker' (exit $code)."
    }
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        Wait-Process -Id $process.Id -ErrorAction SilentlyContinue
    }
    return 0
}

if ([System.IO.Path]::GetExtension($sourcePath) -ine '.dwg') {
    throw 'Native conversion requires a .dwg source.'
}
if (-not (Test-Path -LiteralPath $coreConsole)) {
    throw "AutoCAD 2026 Core Console not found: $coreConsole"
}

$needsBuild = -not (Test-Path -LiteralPath $pluginPath)
if (-not $needsBuild) {
    $pluginTime = (Get-Item -LiteralPath $pluginPath).LastWriteTimeUtc
    $needsBuild = @(Get-ChildItem -LiteralPath $projectRoot -File | Where-Object { $_.Extension -in @('.cs', '.csproj') -and $_.LastWriteTimeUtc -gt $pluginTime }).Count -gt 0
}
if ($needsBuild) {
    dotnet build $projectPath -c Release --nologo
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pluginPath)) {
        throw 'Native DWG writer build failed.'
    }
}

$outPath = [System.IO.Path]::GetFullPath($OutFolder)
if (Test-Path -LiteralPath $outPath) {
    throw "Output folder already exists: $outPath"
}
[System.IO.Directory]::CreateDirectory($outPath) | Out-Null

$baseName = [System.IO.Path]::GetFileNameWithoutExtension($sourcePath)
$outputDwg = Join-Path $outPath ($baseName + '_standardized.dwg')
$expectationPath = Join-Path $outPath 'write-expectation.json'
$validationPath = Join-Path $outPath 'validation.json'
$receiptPath = Join-Path $outPath 'mapping.json'
$tracePath = Join-Path $outPath 'native-writer.trace.log'
$scriptPath = Join-Path $outPath 'convert.generated.scr'
$validateScriptPath = Join-Path $outPath 'validate.generated.scr'
$pluginForScript = $pluginPath.Replace('\', '/')
$pluginDirectoryForScript = (Split-Path -Parent $pluginPath).Replace('\','/')
$trustBeforeForScript = (Join-Path $outPath 'trustedpaths.before.txt').Replace('\','/')
$trustedLoad = '(progn (setq recovery-old-trust (getvar "TRUSTEDPATHS")) (setq recovery-f (open "' + $trustBeforeForScript + '" "w")) (write-line recovery-old-trust recovery-f) (close recovery-f) (setvar "TRUSTEDPATHS" (if (= recovery-old-trust "") "' + $pluginDirectoryForScript + '" (strcat recovery-old-trust ";' + $pluginDirectoryForScript + '"))) (setq recovery-result (vl-catch-all-apply (function (lambda () (command "_.NETLOAD" "' + $pluginForScript + '"))) nil)) (setvar "TRUSTEDPATHS" recovery-old-trust) (princ (strcat "\nRECOVERY_TRUST_RESTORED=" (if (equal recovery-old-trust (getvar "TRUSTEDPATHS")) "YES" "NO") "\n")) (if (vl-catch-all-error-p recovery-result) (princ (vl-catch-all-error-message recovery-result))) (princ))'
$scriptLines = @(
    $trustedLoad,
    'LAYERLABCONVERTNATIVE',
    '_.QUIT'
)
[System.IO.File]::WriteAllLines($scriptPath, $scriptLines, [System.Text.UTF8Encoding]::new($false))
$validateScriptLines = @(
    $trustedLoad,
    'LAYERLABVALIDATENATIVE',
    '_.QUIT'
)
[System.IO.File]::WriteAllLines($validateScriptPath, $validateScriptLines, [System.Text.UTF8Encoding]::new($false))

$before = Get-Sha256 $sourcePath
$env:LAYERLAB_SOURCE_PATH = $sourcePath
$env:LAYERLAB_MAPPING_PATH = $mappingPath
$env:LAYERLAB_STANDARD_PATH = $standardPath
$env:LAYERLAB_OUTPUT_PATH = $outputDwg
$env:LAYERLAB_EXPECTATION_OUT = $expectationPath
$env:LAYERLAB_VALIDATION_OUT = $validationPath
$env:LAYERLAB_RECEIPT_OUT = $receiptPath
$env:LAYERLAB_TRACE_OUT = $tracePath

try {
    $writeExitCode = Invoke-CoreConsole -Drawing $sourcePath -Script $scriptPath -SuccessMarker 'command:stage1-pass' -TraceFile $tracePath
    if ($writeExitCode -ne 0) {
        throw "AutoCAD native DWG write stage failed with exit code $writeExitCode."
    }
    foreach ($required in @($outputDwg, $expectationPath)) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Native write stage did not produce required output: $required"
        }
    }

    $released = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $probe = [System.IO.File]::Open($outputDwg, 'Open', 'ReadWrite', 'None')
            $probe.Dispose()
            $released = $true
            break
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $released) {
        throw 'Written DWG remained locked after the write-stage AutoCAD process exited.'
    }

    $validateExitCode = Invoke-CoreConsole -Drawing $outputDwg -Script $validateScriptPath -SuccessMarker 'validate:pass' -TraceFile $tracePath
    if ($validateExitCode -ne 0) {
        throw "AutoCAD native DWG reopen/validation stage failed with exit code $validateExitCode."
    }
}
catch {
    Remove-Item -LiteralPath $outputDwg,$validationPath,$receiptPath -Force -ErrorAction SilentlyContinue
    throw
}
finally {
    Remove-Item Env:LAYERLAB_SOURCE_PATH,Env:LAYERLAB_MAPPING_PATH,Env:LAYERLAB_STANDARD_PATH,Env:LAYERLAB_OUTPUT_PATH,Env:LAYERLAB_EXPECTATION_OUT,Env:LAYERLAB_VALIDATION_OUT,Env:LAYERLAB_RECEIPT_OUT,Env:LAYERLAB_TRACE_OUT -ErrorAction SilentlyContinue
}

$after = Get-Sha256 $sourcePath
if ($before -ne $after) {
    throw 'Source SHA-256 changed during native DWG conversion.'
}
foreach ($required in @($outputDwg, $expectationPath, $validationPath, $receiptPath)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Native conversion did not produce required output: $required"
    }
}
$validation = Get-Content -Raw -LiteralPath $validationPath | ConvertFrom-Json
if ($validation.status -ne 'PASS' -or -not $validation.output.save_reopen -or -not $validation.non_layer_signature_match) {
    throw 'Native DWG validation report did not pass all gates.'
}

[PSCustomObject]@{
    Status = 'PASS'
    SourceUnchanged = $true
    SourceSha256 = $after
    Output = $outputDwg
    OutputSha256 = Get-Sha256 $outputDwg
    Mapping = $receiptPath
    Validation = $validationPath
    WriteExpectation = $expectationPath
    WriteScript = $scriptPath
    ReopenValidationScript = $validateScriptPath
}

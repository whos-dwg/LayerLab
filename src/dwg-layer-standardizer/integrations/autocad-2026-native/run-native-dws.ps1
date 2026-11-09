param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [Parameter(Mandatory = $true)]
    [string]$VerifiedDwg,

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

function Invoke-CoreConsole([string]$Drawing, [string]$Script, [string]$SuccessMarker, [string]$TraceFile) {
    $arguments = @('/i', ('"' + $Drawing + '"'), '/s', ('"' + $Script + '"'), '/l', 'cht')
    $process = Start-Process -FilePath $coreConsole -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -WorkingDirectory (Split-Path -Parent $Script) `
        -RedirectStandardOutput ($Script + '.stdout.log') -RedirectStandardError ($Script + '.stderr.log')
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
        throw "AutoCAD DWS stage did not emit success marker '$SuccessMarker' (exit $code)."
    }
    # Let the scripted _.QUIT finish cleanly after the native command reports that
    # its output is durable. Force-stop only a console that remains hung.
    if (-not $process.HasExited) {
        Wait-Process -Id $process.Id -Timeout 20 -ErrorAction SilentlyContinue
        $process.Refresh()
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
            Wait-Process -Id $process.Id -ErrorAction SilentlyContinue
        }
    }
}

$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$verifiedDwgPath = (Resolve-Path -LiteralPath $VerifiedDwg).Path
$mappingPath = (Resolve-Path -LiteralPath $Mapping).Path
$standardPath = (Resolve-Path -LiteralPath $Standard).Path
$outPath = (Resolve-Path -LiteralPath $OutFolder).Path
$projectRoot = $PSScriptRoot
$pluginPath = Join-Path $projectRoot 'bin\Release\net10.0-windows\LayerLab.NativeLayerExport.dll'
$projectPath = Join-Path $projectRoot 'LayerLab.NativeLayerExport.csproj'
$coreConsole = 'C:\Program Files\Autodesk\AutoCAD 2026\accoreconsole.exe'
$fullAutoCad = 'C:\Program Files\Autodesk\AutoCAD 2026\acad.exe'
$windowsPowerShell = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$desktopDwsLoader = Join-Path $projectRoot 'run-full-autocad-dws-load.ps1'
$laytransPath = 'C:\Program Files\Autodesk\AutoCAD 2026\Laytrans.arx'

foreach ($dwg in @($sourcePath, $verifiedDwgPath)) {
    if ([System.IO.Path]::GetExtension($dwg) -ine '.dwg') { throw "DWS workflow requires DWG input: $dwg" }
}
foreach ($required in @($coreConsole, $fullAutoCad, $windowsPowerShell, $desktopDwsLoader, $laytransPath)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required AutoCAD component not found: $required" }
}

$needsBuild = -not (Test-Path -LiteralPath $pluginPath)
if (-not $needsBuild) {
    $pluginTime = (Get-Item -LiteralPath $pluginPath).LastWriteTimeUtc
    $needsBuild = @(Get-ChildItem -LiteralPath $projectRoot -File | Where-Object {
        $_.Extension -in @('.cs', '.csproj') -and $_.LastWriteTimeUtc -gt $pluginTime
    }).Count -gt 0
}
if ($needsBuild) {
    dotnet build $projectPath -c Release --nologo
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pluginPath)) {
        throw 'Native DWS adapter build failed.'
    }
}

$baseName = [System.IO.Path]::GetFileNameWithoutExtension($sourcePath)
$dwsPath = Join-Path $outPath ($baseName + '_layer_mapping.dws')
$validationPath = Join-Path $outPath 'dws-validation.json'
$expectationPath = Join-Path $outPath 'dws-expectation.json'
$validationCopy = Join-Path (Split-Path -Parent $sourcePath) `
    ($baseName + '.dws-validation-' + [Guid]::NewGuid().ToString('N') + '.dwg')
$validationAuxiliary = @(
    [System.IO.Path]::ChangeExtension($validationCopy, '.bak'),
    [System.IO.Path]::ChangeExtension($validationCopy, '.dwl'),
    [System.IO.Path]::ChangeExtension($validationCopy, '.dwl2')
)
$tracePath = Join-Path $outPath 'native-dws.trace.log'
$createScript = Join-Path $outPath 'create-dws.generated.scr'
$prepareScript = Join-Path $outPath 'prepare-dws-validation.generated.scr'
$loadScript = Join-Path $outPath 'load-dws.generated.lsp'
$validateScript = Join-Path $outPath 'validate-dws.generated.scr'
$cleanupScript = Join-Path $outPath 'cleanup-dws-validation.generated.scr'
$loadMarker = Join-Path $outPath 'dws-actual-load.marker'
$trustBeforeForScript = (Join-Path $outPath 'dws-trustedpaths.before.txt').Replace('\','/')

foreach ($newPath in @($dwsPath, $validationPath, $expectationPath, $validationCopy)) {
    if (Test-Path -LiteralPath $newPath) { throw "Refusing to overwrite DWS artifact: $newPath" }
}
foreach ($transientPath in @($tracePath, $loadMarker)) {
    Remove-Item -LiteralPath $transientPath -Force -ErrorAction SilentlyContinue
}

$pluginForScript = $pluginPath.Replace('\', '/')
$pluginDirectoryForScript = (Split-Path -Parent $pluginPath).Replace('\','/')
$trustedLoad = '(progn (setq recovery-old-trust (getvar "TRUSTEDPATHS")) (setq recovery-f (open "' + $trustBeforeForScript + '" "w")) (write-line recovery-old-trust recovery-f) (close recovery-f) (setvar "TRUSTEDPATHS" (if (= recovery-old-trust "") "' + $pluginDirectoryForScript + '" (strcat recovery-old-trust ";' + $pluginDirectoryForScript + '"))) (setq recovery-result (vl-catch-all-apply (function (lambda () (command "_.NETLOAD" "' + $pluginForScript + '"))) nil)) (setvar "TRUSTEDPATHS" recovery-old-trust) (if (vl-catch-all-error-p recovery-result) (princ (vl-catch-all-error-message recovery-result))) (princ))'
$createLines = @($trustedLoad, 'LAYERLABCREATEDWS', '_.QUIT')
[System.IO.File]::WriteAllLines($createScript, $createLines, [System.Text.UTF8Encoding]::new($false))

$laytransForScript = $laytransPath.Replace('\', '/')
$dwsForScript = $dwsPath.Replace('\', '/')
$loadMarkerForScript = $loadMarker.Replace('\', '/')
$prepareLines = @($trustedLoad, 'LAYERLABPREPAREDWSVALIDATION', '_.QUIT')
[System.IO.File]::WriteAllLines($prepareScript, $prepareLines, [System.Text.UTF8Encoding]::new($false))
$actualLoad = '(progn (arxload "' + $laytransForScript + '") (if (acet-laytrans "' + $dwsForScript + '" 4) (progn (command "_.QSAVE") (setq layerlab-load-marker (open "' + $loadMarkerForScript + '" "w")) (write-line "LAYERLAB_DWS_LOAD_PASS" layerlab-load-marker) (close layerlab-load-marker)) (princ "LAYERLAB_DWS_LOAD_FAIL")) (princ))'
[System.IO.File]::WriteAllText($loadScript, $actualLoad, [System.Text.UTF8Encoding]::new($false))
$validateLines = @($trustedLoad, 'LAYERLABVALIDATEDWS', '_.QUIT')
[System.IO.File]::WriteAllLines($validateScript, $validateLines, [System.Text.UTF8Encoding]::new($false))
$cleanupLines = @($trustedLoad, 'LAYERLABCLEANUPDWSVALIDATION', '_.QUIT')
[System.IO.File]::WriteAllLines($cleanupScript, $cleanupLines, [System.Text.UTF8Encoding]::new($false))

$sourceBefore = Get-Sha256 $sourcePath
$verifiedDwgBefore = Get-Sha256 $verifiedDwgPath
$env:LAYERLAB_SOURCE_PATH = $sourcePath
$env:LAYERLAB_MAPPING_PATH = $mappingPath
$env:LAYERLAB_STANDARD_PATH = $standardPath
$env:LAYERLAB_DWS_OUTPUT = $dwsPath
$env:LAYERLAB_DWS_EXPECTATION_OUT = $expectationPath
$env:LAYERLAB_DWS_VALIDATION_OUT = $validationPath
$env:LAYERLAB_DWS_LOAD_MARKER = $loadMarker
$env:LAYERLAB_DWS_VALIDATION_COPY = $validationCopy
$env:LAYERLAB_TRACE_OUT = $tracePath

try {
    Invoke-CoreConsole -Drawing $verifiedDwgPath -Script $createScript `
        -SuccessMarker 'dws:create:pass' -TraceFile $tracePath
    if (-not (Test-Path -LiteralPath $dwsPath)) { throw 'DWS creation did not produce an output file.' }

    Copy-Item -LiteralPath $sourcePath -Destination $validationCopy
    Invoke-CoreConsole -Drawing $validationCopy -Script $prepareScript `
        -SuccessMarker 'dws:prepare:pass' -TraceFile $tracePath
    & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File $desktopDwsLoader `
        -Drawing $validationCopy -CommandFile $loadScript -Marker $loadMarker
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $loadMarker)) {
        throw 'Full AutoCAD did not complete actual DWS load validation.'
    }
    Invoke-CoreConsole -Drawing $validationCopy -Script $cleanupScript `
        -SuccessMarker 'dws:cleanup:pass' -TraceFile $tracePath
    Invoke-CoreConsole -Drawing $validationCopy -Script $validateScript `
        -SuccessMarker 'dws:validate:pass' -TraceFile $tracePath

    if ((Get-Sha256 $sourcePath) -ne $sourceBefore) { throw 'Source DWG changed during DWS creation or validation.' }
    if ((Get-Sha256 $verifiedDwgPath) -ne $verifiedDwgBefore) { throw 'Verified primary DWG changed during DWS creation.' }
    foreach ($required in @($dwsPath, $validationPath)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "DWS workflow did not produce required output: $required" }
    }
    $validation = Get-Content -Raw -LiteralPath $validationPath | ConvertFrom-Json
    if ($validation.status -ne 'PASS' -or -not $validation.dws.actual_autocad_load) {
        throw 'DWS validation did not record an actual successful AutoCAD load.'
    }
}
catch {
    Remove-Item -LiteralPath $dwsPath,$validationPath,$expectationPath -Force -ErrorAction SilentlyContinue
    throw
}
finally {
    Remove-Item -LiteralPath $validationCopy,$loadMarker -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $validationAuxiliary -Force -ErrorAction SilentlyContinue
    Remove-Item Env:LAYERLAB_SOURCE_PATH,Env:LAYERLAB_MAPPING_PATH,Env:LAYERLAB_STANDARD_PATH,Env:LAYERLAB_DWS_OUTPUT,Env:LAYERLAB_DWS_EXPECTATION_OUT,Env:LAYERLAB_DWS_VALIDATION_OUT,Env:LAYERLAB_DWS_LOAD_MARKER,Env:LAYERLAB_DWS_VALIDATION_COPY,Env:LAYERLAB_TRACE_OUT -ErrorAction SilentlyContinue
}

[PSCustomObject]@{
    Status = 'PASS'
    PrimaryDwgUnchanged = $true
    SourceUnchanged = $true
    Dws = $dwsPath
    DwsSha256 = Get-Sha256 $dwsPath
    Validation = $validationPath
    ActualAutoCadLoad = $true
}

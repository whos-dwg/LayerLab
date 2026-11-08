param(
    [Parameter(Mandatory = $true)]
    [string]$Drawing,

    [Parameter(Mandatory = $true)]
    [string]$CommandFile,

    [Parameter(Mandatory = $true)]
    [string]$Marker,

    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = 'Stop'

$drawingPath = (Resolve-Path -LiteralPath $Drawing).Path
$commandPath = (Resolve-Path -LiteralPath $CommandFile).Path
$markerPath = [System.IO.Path]::GetFullPath($Marker)
$commandText = [System.IO.File]::ReadAllText($commandPath).Trim()
if (-not $commandText) { throw 'The AutoCAD DWS load command is empty.' }

$script:AutoCadTransientRpcErrors = @(
    -2147418111, # 0x80010001 RPC_E_CALL_REJECTED
    -2147417846  # 0x8001010A RPC_E_SERVERCALL_RETRYLATER
)

function Invoke-AutoCadCom(
    [Parameter(Mandatory = $true)]
    [scriptblock]$Operation,

    [Parameter(Mandatory = $true)]
    [string]$Description,

    [int]$Attempts = 80,
    [int]$DelayMilliseconds = 250
) {
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            return & $Operation
        }
        catch [System.Runtime.InteropServices.COMException] {
            if ($_.Exception.HResult -notin $script:AutoCadTransientRpcErrors) { throw }
            if ($attempt -eq $Attempts) {
                throw "AutoCAD remained busy while $Description after $Attempts attempts: $($_.Exception.Message)"
            }
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
}

$application = $null
$documents = $null
$originalDocument = $null
$validationDocument = $null
$originalPath = $null
$autoCadVersion = $null
try {
    try {
        $application = Invoke-AutoCadCom -Description 'connecting to the AutoCAD 2026 desktop session' -Operation {
            [Runtime.InteropServices.Marshal]::GetActiveObject('AutoCAD.Application.25.1')
        }
    }
    catch {
        throw 'A running AutoCAD 2026 desktop session is required for actual DWS load validation.'
    }
    $autoCadVersion = [string](Invoke-AutoCadCom -Description 'reading the AutoCAD version' -Operation {
        $application.Version
    })
    if (-not $autoCadVersion.StartsWith('25.1', [StringComparison]::OrdinalIgnoreCase)) {
        throw "The running AutoCAD session is not AutoCAD 2026: $autoCadVersion"
    }
    $documents = Invoke-AutoCadCom -Description 'accessing the AutoCAD document collection' -Operation {
        # A COM Documents collection is enumerable. Keep it as one automation
        # object instead of allowing PowerShell to unwrap it into Document items.
        Write-Output -NoEnumerate $application.Documents
    }
    $documentCount = [int](Invoke-AutoCadCom -Description 'counting open AutoCAD documents' -Operation {
        $documents.Count
    })
    if ($documentCount -gt 0) {
        $originalDocument = Invoke-AutoCadCom -Description 'reading the active AutoCAD document' -Operation {
            $application.ActiveDocument
        }
        $originalPath = [string](Invoke-AutoCadCom -Description 'reading the active document path' -Operation {
            $originalDocument.FullName
        })
        $activeCommands = [string](Invoke-AutoCadCom -Description 'checking whether AutoCAD is idle' -Operation {
            $originalDocument.GetVariable('CMDNAMES')
        })
        if (-not [string]::IsNullOrWhiteSpace($activeCommands)) {
            throw "AutoCAD is busy with an active command: $activeCommands"
        }
    }
    for ($index = 0; $index -lt $documentCount; $index++) {
        $openDocument = Invoke-AutoCadCom -Description "reading open AutoCAD document $index" -Operation {
            $documents.Item($index)
        }
        try {
            $openPath = [string](Invoke-AutoCadCom -Description "reading open AutoCAD document $index path" -Operation {
                $openDocument.FullName
            })
            if ([string]::Equals($openPath, $drawingPath,
                    [StringComparison]::OrdinalIgnoreCase)) {
                throw 'The disposable DWS validation copy is already open in AutoCAD.'
            }
        }
        finally {
            [Runtime.InteropServices.Marshal]::ReleaseComObject($openDocument) | Out-Null
        }
    }

    $validationDocument = Invoke-AutoCadCom -Description 'opening the disposable DWS validation drawing' -Operation {
        $documents.Open($drawingPath, $false)
    }
    Invoke-AutoCadCom -Description 'activating the disposable DWS validation drawing' -Operation {
        $validationDocument.Activate()
    }
    Invoke-AutoCadCom -Description 'sending the DWS load command to AutoCAD' -Operation {
        $validationDocument.SendCommand($commandText + "`r")
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline -and -not (Test-Path -LiteralPath $markerPath)) {
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path -LiteralPath $markerPath)) {
        throw 'AutoCAD did not report a successful DWS load before the timeout.'
    }
    $markerValue = [System.IO.File]::ReadAllText($markerPath).Trim()
    if ($markerValue -ne 'LAYERLAB_DWS_LOAD_PASS') {
        throw "AutoCAD reported an invalid DWS load marker: $markerValue"
    }

    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        try {
            $validationCommands = [string](Invoke-AutoCadCom -Description 'waiting for the DWS load command to finish' `
                -Attempts 8 -DelayMilliseconds 250 -Operation {
                    $validationDocument.GetVariable('CMDNAMES')
                })
            if ([string]::IsNullOrWhiteSpace($validationCommands)) { break }
        }
        catch { }
        Start-Sleep -Milliseconds 250
    }
    $validationCommands = [string](Invoke-AutoCadCom -Description 'confirming that the DWS load command finished' `
        -Operation { $validationDocument.GetVariable('CMDNAMES') })
    if (-not [string]::IsNullOrWhiteSpace($validationCommands)) {
        throw 'AutoCAD remained busy after writing the DWS success marker.'
    }

    Invoke-AutoCadCom -Description 'closing the disposable DWS validation drawing' -Operation {
        $validationDocument.Close($false)
    }
    [Runtime.InteropServices.Marshal]::ReleaseComObject($validationDocument) | Out-Null
    $validationDocument = $null
    if ($null -ne $originalDocument) {
        Invoke-AutoCadCom -Description 'restoring the original active AutoCAD document' -Operation {
            $originalDocument.Activate()
        }
    }
    [PSCustomObject]@{
        Status = 'PASS'
        AutoCadVersion = $autoCadVersion
        OriginalDocument = $originalPath
        ValidationCopy = $drawingPath
        ActualDwsLoad = $true
    } | ConvertTo-Json -Compress
}
finally {
    if ($null -ne $validationDocument) {
        try {
            Invoke-AutoCadCom -Description 'closing the disposable validation drawing during cleanup' `
                -Attempts 20 -DelayMilliseconds 250 -Operation { $validationDocument.Close($false) }
        } catch { }
        try { [Runtime.InteropServices.Marshal]::ReleaseComObject($validationDocument) | Out-Null } catch { }
    }
    if ($null -ne $originalDocument) {
        try {
            Invoke-AutoCadCom -Description 'restoring the original document during cleanup' `
                -Attempts 20 -DelayMilliseconds 250 -Operation { $originalDocument.Activate() }
        } catch { }
        try { [Runtime.InteropServices.Marshal]::ReleaseComObject($originalDocument) | Out-Null } catch { }
    }
    if ($null -ne $documents) {
        try { [Runtime.InteropServices.Marshal]::ReleaseComObject($documents) | Out-Null } catch { }
    }
    if ($null -ne $application) {
        try { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) | Out-Null } catch { }
    }
}

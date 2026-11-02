param([Parameter(Mandatory=$true)][string]$Config)
$ErrorActionPreference = 'Stop'
$vlbResult = [System.IO.Path]::ChangeExtension($Config, '.result.json')
$vlbReceipt = @{ state='FAILED'; createdMapping=$false; createdFirewallRule=$false }
try {
    $vlbCfg = Get-Content -Raw -LiteralPath $Config | ConvertFrom-Json
    if ($vlbCfg.node_id -notmatch '^[a-z0-9_-]{1,64}$') { throw 'Invalid node ID' }
    foreach ($vlbField in @('listen_ip','vm_ip','controller_ip')) {
        $vlbParsed = [System.Net.IPAddress]::Parse([string]$vlbCfg.$vlbField)
        if ($vlbParsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or $vlbCfg.$vlbField -match '^(0\.|127\.|169\.254\.)' -or $vlbParsed.GetAddressBytes()[0] -ge 224 -or $vlbParsed.ToString() -ne [string]$vlbCfg.$vlbField) { throw "Invalid IPv4: $vlbField" }
    }
    $vlbPort = [int]$vlbCfg.listen_port
    if ($vlbPort -lt 1 -or $vlbPort -gt 65535) { throw 'Invalid port' }
    if (-not (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -eq $vlbCfg.listen_ip })) { throw 'Listen IP is not assigned to this Windows computer' }
    $vlbIdentity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $vlbIdentity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator rights are required' }
    $vlbReg = 'HKLM:\SYSTEM\CurrentControlSet\Services\PortProxy\v4tov4\tcp'
    $vlbMappingName = "$($vlbCfg.listen_ip)/$vlbPort"
    $vlbExpected = "$($vlbCfg.vm_ip)/22"
    $vlbOld = $null
    if (Test-Path -LiteralPath $vlbReg) {
        $vlbProp = Get-ItemProperty -LiteralPath $vlbReg
        $vlbProperty = $vlbProp.PSObject.Properties[$vlbMappingName]
        if ($vlbProperty) { $vlbOld = [string]$vlbProperty.Value }
    }
    if ($vlbOld -and $vlbOld -ne $vlbExpected) { throw 'Existing port mapping has a different target; left unchanged' }
    $vlbRuleName = "VirtuosoLANBridge-$($vlbCfg.node_id)-SSH"
    $vlbRule = Get-NetFirewallRule -Name $vlbRuleName -ErrorAction SilentlyContinue
    if ($vlbRule) {
        $vlbAddr = $vlbRule | Get-NetFirewallAddressFilter
        $vlbPorts = $vlbRule | Get-NetFirewallPortFilter
        if ([string]$vlbAddr.LocalAddress -ne $vlbCfg.listen_ip -or [string]$vlbAddr.RemoteAddress -ne $vlbCfg.controller_ip -or [string]$vlbPorts.LocalPort -ne [string]$vlbPort -or [string]$vlbPorts.Protocol -ne 'TCP' -or [string]$vlbRule.Action -ne 'Allow' -or [string]$vlbRule.Direction -ne 'Inbound' -or [string]$vlbRule.Enabled -ne 'True') {
            throw 'Existing managed firewall rule differs; left unchanged'
        }
    }
    if (-not $vlbOld) {
        if (Get-NetTCPConnection -State Listen -LocalPort $vlbPort -ErrorAction SilentlyContinue) { throw 'Requested port already has a listener; left unchanged' }
        & netsh.exe interface portproxy add v4tov4 "listenaddress=$($vlbCfg.listen_ip)" "listenport=$vlbPort" "connectaddress=$($vlbCfg.vm_ip)" 'connectport=22' 'protocol=tcp'
        if ($LASTEXITCODE -ne 0) { throw 'netsh failed' }
        $vlbReceipt.createdMapping = $true
    }
    Start-Service iphlpsvc
    if (-not $vlbRule) {
        New-NetFirewallRule -Name $vlbRuleName -DisplayName "Virtuoso LAN Bridge SSH ($($vlbCfg.node_id))" -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $vlbCfg.listen_ip -LocalPort $vlbPort -RemoteAddress $vlbCfg.controller_ip -Profile Any | Out-Null
        $vlbReceipt.createdFirewallRule = $true
    }
    $vlbSaved = Get-ItemProperty -LiteralPath $vlbReg
    if ([string]$vlbSaved.PSObject.Properties[$vlbMappingName].Value -ne $vlbExpected) { throw 'Mapping verification failed' }
    $vlbReceipt.state = 'CONFIGURED'
    $vlbReceipt.endpoint = "$($vlbCfg.listen_ip):$vlbPort"
    $vlbReceipt.target = "$($vlbCfg.vm_ip):22"
    $vlbReceipt.allowedSource = $vlbCfg.controller_ip
    $vlbReceipt.firewallRule = $vlbRuleName
    $vlbReceipt.note = 'Controller must verify cross-computer SSH; local configuration is not proof of remote reachability.'
} catch {
    $vlbReceipt.error = $_.Exception.Message
    $vlbReceipt.note = 'Partial changes, if any, are recorded. Existing rules were not removed or broadened.'
} finally {
    $vlbReceipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $vlbResult -Encoding UTF8
}
if ($vlbReceipt.state -ne 'CONFIGURED') { exit 1 }

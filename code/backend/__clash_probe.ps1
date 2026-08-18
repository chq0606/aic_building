$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== Installed programs (Clash) ==="
$paths = @(
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
Get-ItemProperty $paths -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -match 'Clash|Verge|mihomo' } |
  Select-Object DisplayName, DisplayVersion, InstallLocation, UninstallString |
  Format-List

Write-Host "`n=== Directories ==="
$searchRoots = @(
  'C:\Program Files',
  'C:\Program Files (x86)',
  $env:LOCALAPPDATA,
  $env:APPDATA,
  $env:USERPROFILE,
  'C:\Users\Public'
)
foreach ($root in $searchRoots) {
  Get-ChildItem -Path $root -Filter '*Clash*' -Directory -ErrorAction SilentlyContinue |
    Select-Object FullName
  Get-ChildItem -Path $root -Filter '*Verge*' -Directory -ErrorAction SilentlyContinue |
    Select-Object FullName
  Get-ChildItem -Path $root -Filter '*mihomo*' -Directory -ErrorAction SilentlyContinue |
    Select-Object FullName
}

Write-Host "`n=== Processes ==="
Get-Process -ErrorAction SilentlyContinue |
  Where-Object { $_.ProcessName -match 'clash|verge|mihomo' } |
  Select-Object Name, Id, Path |
  Format-List

Write-Host "`n=== Services ==="
Get-Service -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match 'clash|verge|mihomo' -or $_.DisplayName -match 'Clash|Verge|mihomo' } |
  Select-Object Name, Status, StartType |
  Format-List

Write-Host "`n=== Scheduled tasks ==="
Get-ScheduledTask -ErrorAction SilentlyContinue |
  Where-Object { $_.TaskName -match 'Clash|Verge|mihomo' } |
  Select-Object TaskName, State |
  Format-List

Write-Host "`n=== Start menu shortcuts ==="
$startMenus = @(
  "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
  "$env:ProgramData\Microsoft\Windows\Start Menu\Programs"
)
foreach ($sm in $startMenus) {
  Get-ChildItem -Path $sm -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'Clash|Verge' } |
    Select-Object FullName
}

Write-Host "`n=== System proxy ==="
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' |
  Select-Object ProxyEnable, ProxyServer, ProxyOverride |
  Format-List

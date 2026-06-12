# Gasi hr_diktat pozadinski servis.
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*hr_diktat.py*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
        Write-Host "Ugasen proces $($_.ProcessId)"
    }

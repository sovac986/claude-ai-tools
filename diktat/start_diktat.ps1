# Pokrece hr_diktat servis nevidljivo u pozadini (singleton mutex u skripti
# sprjecava duplikate, pa je sigurno zvati ovo vise puta).
Start-Process -WindowStyle Hidden `
    -FilePath "C:\Users\Franjo\AppData\Local\Programs\Python\Python313\pythonw.exe" `
    -ArgumentList "D:\ClaudeAI\tools\diktat\hr_diktat.py"

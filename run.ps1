Write-Host "run.ps1 started"
Write-Host "Current dir: $(Get-Location)"

$env:TELEGRAM_BOT_TOKEN="8469893074:AAHp77VghZdeu02vBEQmJPebNamrZpoCpDY"
$env:SPREADSHEET_ID="15H9rCrqNI6Ws3SsSBalUp6x_gYKPxKkqL4tqjTmv7G8"
$env:SHEET_NAME="employees"
$env:GOOGLE_CREDS_FILE="service_account.json"
$env:DRIVERS_SHEET="drivers"
$env:TARGET_SHEET="requests"


Write-Host "Token set? " ($env:TELEGRAM_BOT_TOKEN.Length -gt 0)
Write-Host "Creds file exists? " (Test-Path $env:GOOGLE_CREDS_FILE)

python bot.py

Write-Host "python exited with code $LASTEXITCODE"
Read-Host "Press Enter to close"

$kok = $PSScriptRoot

wt.exe new-tab -d "$kok" powershell -NoExit -Command "& '$kok\nautilus-env\Scripts\Activate.ps1'; python run.py" `; split-pane -H -d "$kok\dashboard\backend" powershell -NoExit -Command "& '$kok\nautilus-env\Scripts\Activate.ps1'; python -m uvicorn app:app --reload --port 8000" `; split-pane -V -d "$kok\dashboard\frontend" powershell -NoExit -Command "npm run dev"

Start-Sleep -Seconds 4
Start-Process "http://127.0.0.1:5173"
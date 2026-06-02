param([string]$Task = "help")

$PYTHON = ".venv\Scripts\python.exe"

function Check-Venv {
    if (-not (Test-Path ".venv")) {
        Write-Host "No venv found - run: .\tasks.ps1 setup" -ForegroundColor Red
        exit 1
    }
}

switch ($Task) {
    "setup" {
        python -m venv .venv
        & $PYTHON -m pip install --upgrade pip
        & $PYTHON -m pip install -r requirements.txt
        Write-Host "`nDone. Available commands:" -ForegroundColor Green
        Write-Host "  .\tasks.ps1 run       - run notifier once"
        Write-Host "  .\tasks.ps1 dev       - run notifier once (DEBUG mode)"
        Write-Host "  .\tasks.ps1 schedule  - start hourly scheduler"
        Write-Host "  .\tasks.ps1 clean     - remove venv"
    }
    "run" {
        Check-Venv
        & $PYTHON notifier.py
    }
    "dev" {
        Check-Venv
        $env:DEBUG = "true"
        & $PYTHON notifier.py
        $env:DEBUG = ""
    }
    "schedule" {
        Check-Venv
        & $PYTHON scheduler.py
    }
    "search" {
        Check-Venv
        if (-not $args[0]) {
            Write-Host "Usage: .\tasks.ps1 search <seloger_url>" -ForegroundColor Red
            exit 1
        }
        & $PYTHON notifier.py $args[0]
    }
    "clean" {
        Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force __pycache__ -ErrorAction SilentlyContinue
        Write-Host "Cleaned." -ForegroundColor Green
    }
    default {
        Write-Host "Usage: .\tasks.ps1 <command>" -ForegroundColor Yellow
        Write-Host "  setup          - create venv and install dependencies"
        Write-Host "  run            - run notifier once (uses SEARCH_URL_n from .env)"
        Write-Host "  dev            - run notifier once (DEBUG mode)"
        Write-Host "  schedule       - start hourly scheduler 08h-22h"
        Write-Host "  search <url>   - force a one-off search for a specific URL"
        Write-Host "  clean          - remove venv"
    }
}

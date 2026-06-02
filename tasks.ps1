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
        Write-Host "  .\tasks.ps1 run       - run once"
        Write-Host "  .\tasks.ps1 dev       - run once (DEBUG mode)"
        Write-Host "  .\tasks.ps1 schedule  - start hourly scheduler"
        Write-Host "  .\tasks.ps1 test      - run test suite"
        Write-Host "  .\tasks.ps1 clean     - remove venv"
    }
    "run" {
        Check-Venv
        & $PYTHON -m immo_bot.core
    }
    "dev" {
        Check-Venv
        $env:DEBUG = "true"
        & $PYTHON -m immo_bot.core
        $env:DEBUG = ""
    }
    "schedule" {
        Check-Venv
        & $PYTHON -m immo_bot.scheduler
    }
    "test" {
        Check-Venv
        & $PYTHON -m pytest tests/ -v
    }
    "clean" {
        Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force __pycache__ -ErrorAction SilentlyContinue
        Get-ChildItem -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Cleaned." -ForegroundColor Green
    }
    default {
        Write-Host "Usage: .\tasks.ps1 <command>" -ForegroundColor Yellow
        Write-Host "  setup     - create venv and install dependencies"
        Write-Host "  run       - run once (uses SEARCH_URL_n from .env)"
        Write-Host "  dev       - run once (DEBUG mode)"
        Write-Host "  schedule  - start hourly scheduler 08h-22h"
        Write-Host "  test      - run test suite"
        Write-Host "  clean     - remove venv"
    }
}

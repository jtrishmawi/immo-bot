---
name: test
description: Run the immo-bot test suite. Use when asked to run tests, check if tests pass, verify a change, or diagnose a test failure.
---

# Run Tests

Run the full test suite for immo-bot using pytest inside the virtual environment.

## Steps

1. Verify the virtual environment exists:
   ```
   .venv/Scripts/python -m pytest --version
   ```
   If missing, run `make setup` first (creates `.venv` and installs `requirements.txt`).

2. Run all tests with verbose output:
   ```
   .venv/Scripts/pytest tests/ -v
   ```
   On Linux/macOS use `.venv/bin/pytest`.

3. Interpret results:
   - All green → change is safe to commit/push.
   - A failure in `test_pap_*` → check `immo_bot/scrapers/pap.py` parse logic.
   - A failure in `test_build_criteria_*` → check `immo_bot/core.py:build_criteria`.
   - A failure in `test_parse_url_*` → check `immo_bot/scrapers/seloger.py:parse_url` / `build_url`.

## What the tests cover

| Test group | File tested |
|---|---|
| `test_parse_url_*`, `test_build_url_*` | `immo_bot/scrapers/seloger.py` |
| `test_build_criteria_*` | `immo_bot/core.py:build_criteria` |
| `test_load_search_urls_*` | `immo_bot/core.py:_load_search_urls` |
| `test_label_*` | `immo_bot/core.py:_label_from_params` |
| `test_pending_search_*` | `immo_bot/platforms/telegram.py` |
| `test_pap_*` | `immo_bot/scrapers/pap.py` |
| `test_detect_site_*` | `immo_bot/core.py:_detect_site` |

## Important note

The test file sets required env vars at the top before any imports:
```python
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test_chat")
os.environ.setdefault("SEARCH_URL_1", "https://www.seloger.com/...")
```
Never remove these — they prevent `sys.exit` from firing during import.

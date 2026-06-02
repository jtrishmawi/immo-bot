.PHONY: setup run dev schedule search clean

ifeq ($(OS),Windows_NT)
  PS = powershell -ExecutionPolicy Bypass -NoProfile -File tasks.ps1

setup:
	$(PS) setup

run:
	$(PS) run

dev:
	$(PS) dev

schedule:
	$(PS) schedule

search:
	$(PS) search "$(URL)"

clean:
	$(PS) clean

else
  PYTHON = .venv/bin/python

setup:
	python -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) notifier.py

dev:
	DEBUG=true $(PYTHON) notifier.py

schedule:
	$(PYTHON) scheduler.py

search:
	$(PYTHON) notifier.py "$(URL)"

clean:
	rm -rf .venv __pycache__ *.pyc

endif

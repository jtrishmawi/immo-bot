.PHONY: setup run dev schedule test clean

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

test:
	$(PS) test

clean:
	$(PS) clean

else
  PYTHON = .venv/bin/python

setup:
	python -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) -m immo_bot.core

dev:
	DEBUG=true $(PYTHON) -m immo_bot.core

schedule:
	$(PYTHON) -m immo_bot.scheduler

test:
	$(PYTHON) -m pytest tests/ -v

clean:
	rm -rf .venv __pycache__ immo_bot/__pycache__ immo_bot/**/__pycache__ *.pyc

endif

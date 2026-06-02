FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY notifier.py seloger_params.py scheduler.py ./

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

CMD ["python", "scheduler.py"]

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY immo_bot/ ./immo_bot/
CMD ["python", "-m", "immo_bot.scheduler"]

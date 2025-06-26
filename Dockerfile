# Dockerfile
FROM python:3.12-slim

WORKDIR /wb_tg_bot

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "wb_tg_bot.py"]

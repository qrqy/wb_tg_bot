# Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY wb_tg_bot/requirements.txt .
RUN pip install -r requirements.txt

COPY wb_tg_bot/ .

CMD ["python", "wb_tg_bot.py"]
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/wake-orchestrator/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY services/wake-orchestrator/app /app/app

RUN useradd -r -s /bin/false appuser \
    && chmod -R a+rX /app
USER appuser

CMD ["python", "-m", "app.main"]

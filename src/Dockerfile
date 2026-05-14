FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos '' appuser && \
    mkdir -p /data /models && \
    chown -R appuser:appuser /app /data /models

USER appuser

ENV PYTHONPATH=/app
ENV DATA_DIR=/data
ENV MODE=A

EXPOSE 8080

CMD ["uvicorn", "capability_matcher.api:app", "--host", "0.0.0.0", "--port", "8080"]

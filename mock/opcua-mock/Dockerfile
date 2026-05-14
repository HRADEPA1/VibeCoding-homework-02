FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY config/ config/

RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app

USER appuser

ENV MOCK_CONFIG=/app/config/montrac_mock.json

EXPOSE 4840

CMD ["python", "server.py"]

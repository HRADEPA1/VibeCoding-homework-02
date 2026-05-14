FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app

USER appuser

ENV TYPEDB_HOST=typedb
ENV TYPEDB_PORT=1729

EXPOSE 8889

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8889"]

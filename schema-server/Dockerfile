FROM python:3.12-slim AS builder
LABEL description="Schema Service"
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/build/deps -r requirements.txt
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 1000 appuser
WORKDIR /app
COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages
COPY app.py .
USER appuser
EXPOSE 7001
CMD ["python", "app.py", "--listen=0.0.0.0:7001"]

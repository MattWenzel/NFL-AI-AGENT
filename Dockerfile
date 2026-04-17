FROM python:3.12-slim

# sqlite3 CLI for in-container backups + dump inspection; ca-certificates
# for HTTPS calls to Anthropic/OpenAI. --no-install-recommends keeps the
# image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        sqlite3 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create the volume mount points inside the image so a fresh container
# (e.g. local docker run without a volume) still has writable dirs. Fly's
# volume mount at /data shadows these in production, which is expected.
RUN mkdir -p /data/runtime /data/nflverse /data/exports

EXPOSE 8080

# --proxy-headers + --forwarded-allow-ips="*" trusts X-Forwarded-For from
# the Fly edge (the only thing that can reach the machine on the private
# network). Without this, the per-IP rate limiter would see every request
# as coming from Fly's internal proxy IP.
CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]

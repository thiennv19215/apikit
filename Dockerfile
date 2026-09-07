FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including ffmpeg for video processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Environment defaults
ENV API_HOST=0.0.0.0
ENV API_PORT=8100
ENV WS_HOST=0.0.0.0
ENV WS_PORT=9222
ENV PYTHONPATH=/app

EXPOSE 8100 9222

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8100/health || exit 1

CMD ["python", "-m", "agent.main"]

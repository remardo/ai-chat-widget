FROM python:3.11-slim

WORKDIR /app

# Coolify healthcheck uses curl/wget inside container
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY backend/app ./app
COPY knowledge ./knowledge
COPY widget ./widget

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8080

# Run application
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "asyncio"]

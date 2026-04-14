# Use a slim Python 3.11 base image
FROM python:3.11-slim

# Force UTF-8 I/O — prevents UnicodeEncodeError with emoji in logs on any host
ENV PYTHONUTF8=1

# Port the app listens on (K8s targets this via the Service)
ENV PORT=8000

# Set working directory inside the container
WORKDIR /app

# Copy and install dependencies first (layer caching - only reinstalls if requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code and config
COPY app.py .
COPY azure_config.py .
COPY config.py .
COPY keywords.py .
COPY utils.py .
COPY database.py .
COPY github_grounding.py .
COPY ai_handlers.py .
COPY teams_poster.py .
COPY orchestrator.py .
COPY response_metrics.py .
# Azure.env is NOT copied — secrets are injected as env vars by Kubernetes
COPY startup.sh .

# Make startup script executable
RUN chmod +x startup.sh

# Expose the default port (App Service overrides via $PORT at runtime)
EXPOSE 8000

# startup.sh reads $PORT (injected by App Service) and falls back to 8000
CMD ["./startup.sh"]

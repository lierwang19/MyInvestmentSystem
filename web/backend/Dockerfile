FROM python:3.11-slim

WORKDIR /app

# Build-time proxy (for pip install) — uses host.docker.internal to reach host proxy
ENV http_proxy=http://host.docker.internal:30810 https_proxy=http://host.docker.internal:30810

# System libs required by opencv-python-headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies (from PyPI via proxy)
COPY web/backend/requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . /app

# Install vendored pytdx3 from the checked-in submodule to keep builds reproducible
RUN pip install --no-cache-dir /app/pytdx3

# Clear proxy env for runtime
ENV http_proxy="" https_proxy=""

# Expose port
EXPOSE 8000

# Set Python path to include root
ENV PYTHONPATH=/app

# Run app
CMD ["uvicorn", "web.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

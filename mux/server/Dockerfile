FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2 / cryptography / tree-sitter
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libffi-dev build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Copy everything first so editable install finds the mux package
COPY pyproject.toml ./
COPY mux ./mux

# Install Python dependencies (editable install so mux/ is importable)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -e .

# Default environment (override on Render dashboard)
ENV DATABASE_URL=""
ENV DEBUG="false"
ENV HOST="0.0.0.0"
ENV PORT="10000"
ENV SANDBOX_PROVIDER="none"
ENV ALLOW_UNVERIFIED_TOKENS="true"
ENV WEB_APP_URL="https://your-frontend.vercel.app"

EXPOSE 10000

CMD ["uvicorn", "mux.main:app", "--host", "0.0.0.0", "--port", "10000"]

# SPDX-License-Identifier: AGPL-3.0-only
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TRACER_PORT=8080
RUN useradd --system --uid 10001 --home-dir /app tracer
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY LICENSE README.md ./
COPY bimtracer ./bimtracer
USER tracer
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200 else 1)"
CMD ["python", "-m", "bimtracer.server"]

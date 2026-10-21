FROM python:3.13-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Only what the service imports: the token verifier and the PostgREST client.
COPY harvey/__init__.py harvey/__init__.py
COPY harvey/craven/__init__.py harvey/craven/config.py harvey/craven/unsubscribe.py harvey/craven/supabase.py harvey/craven/
COPY services/unsubscribe/app.py services/unsubscribe/app.py
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn>=0.34,<1" "httpx>=0.27,<1" "pydantic>=2.5,<3"
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz').status==200 else 1)"
CMD ["uvicorn", "services.unsubscribe.app:app", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers", "--forwarded-allow-ips", "*"]

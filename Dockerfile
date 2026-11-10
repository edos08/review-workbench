FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data

RUN useradd --create-home --uid 1000 app && mkdir /data && chown app:app /data
WORKDIR /app
COPY --chown=app:app server.py store.py mapparser.py auth.py ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --chown=app:app maps ./maps
COPY --chown=app:app static ./static

# No USER line on purpose: the entrypoint fixes the data folder's ownership, then runs the app as
# PUID:PGID (default 1000:1000).
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=2)" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "server.py"]

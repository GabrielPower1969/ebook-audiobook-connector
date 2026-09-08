# Two targets from one file:
#   server  — the reader. Pure standard library, no ML deps. This is what runs on your LAN.
#   cli     — server + faster-whisper, for building books without a Mac.
FROM python:3.12-slim AS server

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AC_BOOKS=/app/books \
    AC_LIBRARY=/app/library \
    AC_CACHE=/app/cache \
    AC_HOST=0.0.0.0 \
    AC_PORT=8765 \
    HOME=/tmp

WORKDIR /app
COPY pyproject.toml README.md ./
COPY audiobook_connector ./audiobook_connector
RUN pip install . && mkdir -p /app/books /app/library /app/cache && chmod 777 /app/library /app/cache

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['AC_PORT']+'/index.json').read()"
CMD ["audiobook-connector", "serve"]


FROM server AS cli
# CTranslate2 + PyAV wheels bring their own ffmpeg, so no apt packages are needed.
ENV HF_HOME=/app/cache/models \
    OMP_NUM_THREADS=4
RUN pip install ".[cpu,formats]"
ENTRYPOINT ["audiobook-connector"]
CMD ["--help"]

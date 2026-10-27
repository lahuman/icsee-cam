FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home camera
WORKDIR /app
COPY requirements-lock.txt ./
RUN python -m pip install -r requirements-lock.txt
COPY pyproject.toml ./
COPY icsee_ptz ./icsee_ptz
RUN python -m pip install --no-deps --no-build-isolation . \
    && mkdir -p /app/data /app/config && chown camera:camera /app/data /app/config
USER camera
ENTRYPOINT ["python", "-m", "icsee_ptz"]
CMD ["serve", "--container", "--port", "8000"]

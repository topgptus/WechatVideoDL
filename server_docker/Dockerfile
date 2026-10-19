FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py browserless_function.js browserless_text_function.js history_store.py user_store.py artifact_names.py job_files.py job_runner.py thumbnail_service.py bundle_service.py presets.py content_quality.py transcription_queue.py ./
COPY templates ./templates
COPY static ./static
RUN mkdir -p /data/downloads

EXPOSE 8080

CMD ["gunicorn","-w","1","--threads","4","--timeout","420","-b","0.0.0.0:8080","app:app"]

FROM python:3.12-slim

# ffmpeg may be used for VRM avatar phase 2 / file-based audio.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY vrm_avatar/ ./vrm_avatar/

# prompts and sessions are mounted from the host via docker-compose
ENV SESSIONS_DIR=/app/sessions
ENV PERSONA_FILE=/app/prompts/persona_odysseus.txt
CMD ["python", "main.py"]

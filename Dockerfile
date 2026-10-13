FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PORT=8080 DATA_DIR=/data/mumbai_work HOME=/data
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && node -v && npm -v
RUN mkdir -p /data/mumbai_work /app && chown -R 1000:1000 /data /app && chmod 777 /data
VOLUME ["/data"]
WORKDIR /app
RUN pip install --no-cache-dir pypdf
COPY --chown=1000:1000 22.py /app/22.py
EXPOSE 8080
USER 1000:1000
CMD ["python", "/app/22.py"]

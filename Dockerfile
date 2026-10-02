FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PORT=8080 DATA_DIR=/data/mumbai_work HOME=/data
# poppler-utils: @@LOOK ke liye (PDF ko image bana ke dekhna)
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/* || true
RUN mkdir -p /data /app && chmod 777 /data
VOLUME ["/data"]
WORKDIR /app
RUN pip install --no-cache-dir pypdf || true
COPY 21.py /app/21.py
EXPOSE 8080
USER 1000:1000
CMD ["python", "/app/21.py"]

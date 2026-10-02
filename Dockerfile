FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PORT=8080 DATA_DIR=/data/mumbai_work HOME=/data
RUN mkdir -p /data /app && chmod 777 /data
VOLUME ["/data"]
WORKDIR /app
RUN pip install --no-cache-dir pypdf || true
COPY 19.py /app/19.py
EXPOSE 8080
USER 1000:1000
CMD ["python", "/app/19.py"]

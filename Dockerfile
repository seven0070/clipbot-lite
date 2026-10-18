FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
RUN mkdir -p /data
ENV DB_PATH=/data/clipbot.db AUTONOMY_GATE=2
EXPOSE 7860
CMD ["python", "server.py"]

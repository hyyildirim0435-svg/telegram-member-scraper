FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py render.yaml README.md .env.example ./
RUN mkdir -p /var/data/sessions
CMD ["python", "main.py"]

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FUNCTION_TARGET=broker
CMD ["functions-framework", "--target=broker", "--port=8080"]

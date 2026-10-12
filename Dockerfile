FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV HOST=0.0.0.0 PORT=8080
EXPOSE 80
CMD ["python", "web_server.py"]

FROM python:3.11-alpine

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

COPY mock_server.py ./mock_server.py
COPY web/production-task.html web/production-task-list.html ./web/
COPY web/production-task.css web/production-task-list.css ./web/
COPY web/production-task.js web/production-task-list.js ./web/

EXPOSE 8080

# Railway injects PORT. This branch serves only the two production-task Mock pages.
CMD ["python", "mock_server.py"]

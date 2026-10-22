FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY status_service ./status_service
COPY tests ./tests

RUN pip install --no-cache-dir -e .

EXPOSE 8080
ENTRYPOINT ["status-service"]
CMD ["8080"]

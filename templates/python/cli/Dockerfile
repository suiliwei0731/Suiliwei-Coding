# 基于官方 slim 镜像，依赖少
FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用 Docker 缓存层）
COPY pyproject.toml README.md ./
COPY cli_validator ./cli_validator
COPY tests ./tests

RUN pip install --no-cache-dir PyYAML>=6.0 \
 && pip install --no-cache-dir -e .

ENTRYPOINT ["cli-validator"]
CMD ["config.yaml"]

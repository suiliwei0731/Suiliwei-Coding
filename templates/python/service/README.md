# status-service

服务模板项目：极简健康状态查询 HTTP API，仅使用 Python 标准库。

## 端点

| 路径 | 方法 | 说明 |
|---|---|---|
| `/healthz` | GET | 存活探针，返回 "OK" |
| `/status`  | GET | 返回版本、启动时长等 JSON |

## 启动

```bash
status-service 8080
```

或直接运行 `python -m status_service.server 8080`。

## 开发

```bash
ruff format --check .
ruff check .
mypy .
pytest -q
python -m build
```

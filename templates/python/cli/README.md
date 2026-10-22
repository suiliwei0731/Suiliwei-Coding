# cli-validator

CLI 模板项目：YAML 配置文件校验器。

## 功能

读取 YAML 配置，校验必填字段、类型、数值范围，输出结构化的违规列表。

## 安装

```bash
pip install -e .
```

## 使用

```bash
cli-validator config.yaml
```

返回码：0 全部通过；1 存在违规；2 用法错误。

## 开发

```bash
ruff format --check .
ruff check .
mypy .
pytest -q
python -m build
```

# PROJECT_CONTEXT.md（环境自检报告）

> 由 `scripts/bootstrap/check_env.py` 生成于 2026-10-21T20:34:25
> 本文件为真实环境快照；每次运行覆盖。标记「未安装/未配置/未验证」的项不代表可用。

## 基础环境

- Python：Python 3.12.10（C:\Users\s\AppData\Local\Programs\Python\Python312\python.exe）
- 磁盘（C:）：总 222.0 GiB / 可用 155.9 GiB
- 网络（外联 pypi.org:443）：可用（pypi.org:443 连接成功）
- 模型端点：未配置（未验证）— 未设置 AI_DEV_PLATFORM_MODEL_BASE_URL

## 外部工具

| 工具 | 状态 | 版本 | 路径 |
|---|---|---|---|
| git | 未安装 | — | — |
| node | 未安装 | — | — |
| docker | 未安装 | — | — |

## Python 包（平台开发依赖）

| 包 | 已安装 |
|---|---|
| yaml | 否 |
| pytest | 否 |
| ruff | 否 |
| mypy | 否 |

## 对实施计划的影响

- Git 未安装时：`git init`/首次提交标注「未验证」，安装后补执行（见 CONTRIBUTING.md）。
- 模型端点未配置符合预期（Phase 1–2 使用模拟适配器，Phase 4 才接入真实模型，见 ADR-002）。
- Node/Docker 为后续阶段（P6/P5）依赖，当前缺失不阻塞 Phase 0。

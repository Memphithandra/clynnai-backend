# ClynnAI 后端

[English](README.md) | 简体中文

ClynnAI 后端是为 ClynnAI Android 应用准备的可移植 FastAPI 服务。它提供 OpenAI 兼容的聊天与图像代理接口、有状态会话 API、基于 LangGraph 的内置 Agent Runtime、Firecrawl 网页工具、文件上传、ASR 转发，以及一个轻量级管理配置页面。

## 功能特性

- **FastAPI 服务**：提供健康检查、版本、管理页、模型、聊天、图像、上传、ASR 和会话接口。
- **OpenAI 兼容上游代理**：支持 `/api/chat/completions` 和 `/api/images/generations`。
- **Agent Runtime**：基于 LangGraph，支持工具调用、流式事件、图片结果提取和手机动作中断。
- **内置工具**：Firecrawl 搜索/抓取、图像生成、手机动作请求。
- **会话存储**：使用 SQLite 保存会话，并使用本地目录保存上传文件。
- **管理 WebUI**：`/admin` 页面可编辑 provider 配置，同时避免在 server-info 中泄露密钥原文。

## 仓库结构

```text
clynnai_backend/
  main.py                 # FastAPI 应用与 API 路由
  config.py               # 设置项、环境变量别名、provider 配置加载
  agent/                  # Agent Runtime、提示词、结构定义、工具、LLM 客户端
data/
  provider-config.example.json
tests/                    # API、配置、Agent Runtime 的 pytest 测试
pyproject.toml
requirements.lock
```

以下运行时文件会被 Git 忽略：

- `.env`
- `data/provider-config.json`
- `.venv/`
- 缓存、日志、本地数据库和构建产物

## 环境要求

- Python 3.11+
- 虚拟环境工具，例如 `uv`、`python -m venv` 或同类工具
- 用于聊天/图像/ASR 的 OpenAI 兼容上游 API
- 可选：Firecrawl API Key，用于网页搜索/抓取工具

## 快速开始

```bash
git clone https://github.com/Memphithandra/clynnai-backend.git
cd clynnai-backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp data/provider-config.example.json data/provider-config.json
python -m clynnai_backend
```

服务默认监听 `0.0.0.0:8088`。

常用地址：

- 健康检查：`http://127.0.0.1:8088/health`
- 版本信息：`http://127.0.0.1:8088/version`
- 管理页面：`http://127.0.0.1:8088/admin`

## 配置说明

配置来源有三类：

1. 环境变量和 `.env`
2. `data/provider-config.json`
3. `clynnai_backend/config.py` 中的内置默认值

环境变量优先级高于 provider 配置文件。

常用配置：

| 配置项 | 环境变量别名 | 默认值 |
| --- | --- | --- |
| `host` | `HOST`, `CLYNN_AI_HOST` | `0.0.0.0` |
| `port` | `PORT`, `CLYNN_AI_PORT` | `8088` |
| `public_base_url` | `PUBLIC_BASE_URL`, `CLYNN_PUBLIC_BASE_URL` | `http://127.0.0.1:8088` |
| `upstream_base_url` | `UPSTREAM_BASE_URL`, `CLYNN_UPSTREAM_BASE_URL` | OpenAI 兼容 base URL |
| `upstream_api_key` | `UPSTREAM_API_KEY`, `CLYNN_UPSTREAM_API_KEY` | 空 |
| `default_model` | `DEFAULT_MODEL`, `CLYNN_DEFAULT_MODEL` | 空 |
| `image_model` | `IMAGE_MODEL`, `CLYNN_IMAGE_MODEL` | 空 |
| `asr_model` | `ASR_MODEL`, `CLYNN_ASR_MODEL` | `whisper-1` |
| `firecrawl_api_key` | `FIRECRAWL_API_KEY`, `CLYNN_FIRECRAWL_API_KEY` | 空 |
| `firecrawl_base_url` | `FIRECRAWL_BASE_URL`, `CLYNN_FIRECRAWL_BASE_URL` | `https://api.firecrawl.dev` |
| `agent_max_steps` | `AGENT_MAX_STEPS`, `CLYNN_AGENT_MAX_STEPS` | `6` |
| `agent_use_native_tool_calls` | `AGENT_USE_NATIVE_TOOL_CALLS`, `CLYNN_AGENT_USE_NATIVE_TOOL_CALLS` | `true` |

provider 配置路径默认为 `./data/provider-config.json`。可以通过下面的环境变量覆盖：

```bash
export CLYNN_CONFIG_PATH=/path/to/provider-config.json
```

## Provider 配置示例

```json
{
  "upstream_base_url": "https://api.example.com/v1",
  "upstream_model": "example-model",
  "upstream_api_key": "",
  "admin_token": "change-me",
  "app_token": "change-me",
  "firecrawl_api_key": "",
  "asr_model": "whisper-1"
}
```

不要提交真实 API Key。真实值请放在 `.env` 或 `data/provider-config.json`。

## API 概览

### 服务与管理

- `GET /health` — 服务健康检查。
- `GET /version` — 后端版本。
- `GET /admin` — 轻量级 provider 配置页面。
- `GET /api/admin/provider-config` — 获取脱敏后的 provider 配置。
- `PUT /api/admin/provider-config` — 更新 provider 配置字段。
- `GET /api/server-info` — 给 App 使用的安全服务信息。
- `GET /api/models` — 可用时列出上游模型。

### OpenAI 兼容代理

- `POST /api/chat/completions` — 转发聊天补全请求到配置的上游。
- `POST /api/images/generations` — 转发图像生成请求。

### ClynnAI 会话 API

- `POST /api/conversations/message` — 发送消息并获得结构化回复。
- `POST /api/conversations/message/stream` — 流式返回 Agent/模型事件。
- `GET /api/conversations` — 列出最近会话。
- `GET /api/conversations/{session_id}/messages` — 列出指定会话消息。
- `POST /api/conversations/{session_id}/messages/{message_id}/edit` — 编辑消息并从该消息重新运行。
- `DELETE /api/conversations/{session_id}` — 删除会话。
- `POST /api/conversations/{session_id}/phone-actions/{action_id}/result` — 上报手机动作执行结果。

### 文件与 ASR

- `POST /api/uploads` — 上传文件。
- `GET /api/uploads/{file_id}` — 获取已上传文件。
- `POST /api/asr/transcribe` — 将音频文件转发给配置的 ASR 模型。

### Persona

- `GET /api/personas` — 列出 persona。
- `PUT /api/personas/{name}` — 创建或更新 persona。
- `DELETE /api/personas/{name}` — 删除 persona。

## 开发

安装测试依赖并运行测试：

```bash
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

当前预期结果：

```text
14 passed
```

## 安全注意事项

- 不要提交 `.env` 或 `data/provider-config.json`。
- `server-info` 和管理配置响应只暴露密钥字段的 `_set` 布尔值，不返回密钥原文。
- 暴露到网络前，请设置强 `admin_token` 和 `app_token`。
- 公开部署时建议使用 HTTPS 反向代理。
- `public_base_url` 应与 Android App 实际访问的 URL 保持一致。

## 许可证

当前仓库还没有包含许可证文件。公开分发前建议补充许可证。

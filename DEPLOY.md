# 腾讯云生产部署说明

本文说明如何在腾讯云主机或容器环境中运行 Research Assistant，并为飞书事件回调联调做好准备。示例只使用变量名和占位说明，不包含真实密钥。

## 环境要求

- Linux 服务器（推荐腾讯云 Linux）或支持 Docker Compose 的运行环境
- Python 3.11 及以上；使用容器部署时无需在宿主机单独安装 Python
- 可访问 Kimi、DeepSeek、Tavily 和飞书开放平台的 HTTPS 出站网络
- 对外可访问的 HTTPS 域名，用于接收飞书事件回调
- 安全组或防火墙放行反向代理所需的 443 端口；应用服务端口建议仅对本机或内网开放

## 安装依赖

直接运行：

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

使用 Docker：

```bash
docker compose build
```

## 环境变量配置

生产启动检查要求以下变量存在：

| 变量 | 用途 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书应用标识 |
| `FEISHU_APP_SECRET` | 飞书应用密钥 |
| `KIMI_API_KEY` | Kimi 模型访问密钥 |
| `DEEPSEEK_API_KEY` | DeepSeek 模型访问密钥 |
| `TAVILY_API_KEY` | 公开信息检索访问密钥 |

常用运行变量包括 `APP_ENV=production`、`HOST=0.0.0.0`、`PORT=8000`。模型地址、模型名称和飞书多维表格相关变量继续沿用项目现有配置，不应在迁移时更换已有 `table_id`。

不要把 `.env` 或真实密钥提交到 Git。生产环境应通过腾讯云密钥管理、容器环境变量或受控的部署配置注入。可以先执行以下命令验证配置；输出只会显示缺少的变量名，不会显示变量值：

```bash
python -m app.deployment.check
```

## 启动方式

直接启动长期运行服务：

```bash
sh scripts/start.sh
```

使用 Docker Compose：

```bash
docker compose up -d --build
```

启动脚本会先进行生产环境检查，检查通过后再启动 `python -m app.server`。检查失败时进程会以非零状态退出，并明确列出缺少的变量名。

## 健康检查

服务启动后执行：

```bash
curl --fail http://127.0.0.1:8000/health
```

正常响应：

```json
{"status":"ok"}
```

也可以运行 `sh scripts/health_check.sh`。部署平台应使用 `/health` 作为存活探针；公网 HTTPS 由反向代理或负载均衡终止后，再转发至应用端口。

## 飞书回调配置

1. 在飞书开放平台为应用启用机器人能力，并完成接收消息所需权限和版本发布。
2. 在“事件与回调”中选择将事件发送至开发者服务器，并配置 HTTPS 请求地址：`https://<你的域名>/feishu/events`。
3. 订阅消息接收事件 `im.message.receive_v1`。当前适配层处理文本消息，其他消息类型会返回清晰的暂不支持提示。
4. 首次保存回调地址时，服务会原样返回飞书发送的 `challenge`，用于地址验证。
5. 联调时先检查 `/health`，再向机器人发送“分析XX基金”或“对新能源行业做深度研究”等文本消息。
6. 确保公网入口使用有效 TLS 证书，并在安全组、反向代理和负载均衡中允许飞书访问回调地址。

飞书应用标识、应用密钥、多维表格标识和模型密钥不应写入回调 URL、日志或错误响应。

## 常见排查顺序

1. 运行 `python -m app.deployment.check`，确认所有生产集成配置就绪。
2. 检查 `/health` 是否返回 `status: ok`。
3. 检查 HTTPS 域名是否能从公网访问 `/feishu/events`。
4. 检查飞书应用是否已发布、事件是否已订阅、机器人权限是否生效。
5. 查看统一格式的应用日志；日志会隐藏疑似密钥内容。

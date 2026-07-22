# Feishu Research Assistant

面向飞书场景的专业投研助手。系统通过统一任务路由调度内部知识、公开信息证据和模型能力，日报仅作为兼容功能保留，不再是默认核心入口。

## 当前能力

- **固收研究**：支持利率、信用、城投等主题的研究任务与材料整合。
- **基金分析**：结合基金材料和公开证据生成基金投资决策意见，并对缺失信息作明确说明。
- **舆情搜索**：通过公共搜索 Provider 获取新闻、公告和监管线索。
- **深度报告**：围绕公司、行业和宏观主题形成结构化研究报告。
- **Evidence 管理**：外部公开信息统一转换为 Evidence，保留标题、内容、来源、时间和链接后再进入模型。

## 核心链路

```text
飞书消息或 HTTP 请求
        ↓
Research Assistant Router
        ↓
Public Search / Tavily
        ↓
Evidence Pool + 内部知识材料
        ↓
Kimi / DeepSeek
        ↓
结构化研究结果
```

所有互联网事实必须经过 Evidence Pool；没有来源的信息不能作为事实输出。业务 Service 不直接处理飞书事件，部署入口也不改变既有研究逻辑。

## 本地运行

```bash
python -m pip install -r requirements.txt
python -m app.server
```

主要接口：

- `GET /health`：服务健康检查
- `POST /research`：结构化研究任务入口
- `POST /feishu/events`：飞书事件回调入口

生产环境配置、腾讯云启动方式和飞书回调联调步骤见 [DEPLOY.md](DEPLOY.md)。请勿提交 `.env`、真实 API Key 或飞书密钥。

## 测试

```bash
python -m py_compile app/main.py
python -m unittest discover -s tests
```

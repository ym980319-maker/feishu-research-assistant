from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from datetime import datetime
import os
import json
import re
import uuid
import httpx
import time
from pathlib import Path

load_dotenv()

app = FastAPI(title="Feishu Research Assistant")

# 去重：避免飞书重试导致同一条消息被处理多次
# PROCESSING_MESSAGE_IDS：正在处理中的消息
# PROCESSED_MESSAGE_IDS：已经完整处理成功的消息
PROCESSING_MESSAGE_IDS = set()
PROCESSED_MESSAGE_IDS = set()


FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

KIMI_API_KEY = os.getenv("KIMI_API_KEY")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k2.6")

FEISHU_BITABLE_APP_TOKEN = os.getenv("FEISHU_BITABLE_APP_TOKEN")
FEISHU_NEWS_TABLE_ID = os.getenv("FEISHU_NEWS_TABLE_ID")
FEISHU_TASK_TABLE_ID = os.getenv("FEISHU_TASK_TABLE_ID")
FEISHU_REPORT_TABLE_ID = os.getenv("FEISHU_REPORT_TABLE_ID")
FEISHU_KNOWLEDGE_TABLE_ID = os.getenv("FEISHU_KNOWLEDGE_TABLE_ID")
FEISHU_MARKET_TABLE_ID = os.getenv("FEISHU_MARKET_TABLE_ID")
FEISHU_DOC_FOLDER_TOKEN = os.getenv("FEISHU_DOC_FOLDER_TOKEN")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "feishu-research-assistant"}


def now_ms() -> int:
    return int(time.time() * 1000)


async def get_tenant_access_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")

    return data["tenant_access_token"]


async def reply_feishu_message(message_id: str, text: str):
    token = await get_tenant_access_token()

    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, json=payload)
        data = resp.json()

    print("Reply result:", data)
    return data


async def create_bitable_record(table_id: str, fields: dict):
    if not FEISHU_BITABLE_APP_TOKEN:
        print("未配置 FEISHU_BITABLE_APP_TOKEN，跳过写入多维表格")
        return None

    token = await get_tenant_access_token()

    url = (
        "https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{table_id}/records"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    payload = {"fields": fields}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, headers=headers, json=payload)
        data = resp.json()

    print("Bitable create result:", data)
    return data


def clean_feishu_mention(text: str) -> str:
    return text.replace("@_user_1", "").strip()



def extract_file_info_from_message(message: dict) -> dict:
    """
    解析飞书文件消息。
    不同飞书消息版本字段可能略有差异，这里尽量兼容。
    """
    content_raw = message.get("content", "{}")

    try:
        content = json.loads(content_raw)
    except Exception:
        content = {}

    file_info = {
        "message_id": message.get("message_id", ""),
        "message_type": message.get("message_type", ""),
        "file_key": content.get("file_key") or content.get("fileKey") or "",
        "file_name": content.get("file_name") or content.get("fileName") or content.get("name") or "",
        "file_size": content.get("file_size") or content.get("size") or "",
        "raw_content": content,
    }

    return file_info



def safe_filename(name: str) -> str:
    name = name or "feishu_file"
    for ch in ['/', '\\\\', ':', '*', '?', '"', '<', '>', '|']:
        name = name.replace(ch, "_")
    return name[:120]


async def download_feishu_message_file(message_id: str, file_key: str, file_name: str) -> str:
    """
    下载飞书消息中的文件资源到本地 downloads 目录。
    """
    token = await get_tenant_access_token()

    os.makedirs("downloads", exist_ok=True)

    safe_name = safe_filename(file_name)
    local_path = os.path.join("downloads", safe_name)

    url = (
        "https://open.feishu.cn/open-apis/im/v1/messages/"
        f"{message_id}/resources/{file_key}"
    )

    headers = {
        "Authorization": f"Bearer {token}",
    }

    params = {
        "type": "file"
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        raise RuntimeError(f"下载文件失败，HTTP {resp.status_code}: {resp.text[:500]}")

    with open(local_path, "wb") as f:
        f.write(resp.content)

    print("Downloaded Feishu file:", local_path, "size:", len(resp.content))
    return local_path


def detect_task_type(user_text: str) -> str:
    if any(k in user_text for k in ["舆情", "新闻", "负面", "正面", "事件", "跟踪"]):
        return "舆情梳理"
    if any(k in user_text for k in ["深度报告", "报告", "研究", "分析框架"]):
        return "深度报告"
    if any(k in user_text for k in ["研报", "摘要", "总结", "提炼", "资料", "材料", "纪要", "核心结论", "投资逻辑"]):
        return "研报摘要"
    return "普通问答"


def extract_json_from_text(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass

    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
    except Exception:
        pass

    return {}


async def call_deepseek(user_text: str, task_type: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "DeepSeek API Key 还没有配置，请先在服务器 .env 里配置 DEEPSEEK_API_KEY。"

    url = f"{DEEPSEEK_BASE_URL}/chat/completions"

    system_prompt = """
你是一个金融投研助手，服务对象是金融从业者。

通用要求：
- 必须用中文回答。
- 不要编造事实。
- 信息不足时，明确写“信息不足”。
- 涉及投资判断时，必须提示“需人工确认”。
- 回答要结构化，适合直接复制到飞书文档或多维表格。
"""

    if task_type == "舆情梳理":
        user_prompt = f"""
请按金融投研舆情格式，梳理以下需求：

用户需求：
{user_text}

请严格按以下格式输出：

【舆情梳理】

一、核心结论
用 3-5 句话总结整体舆情方向、核心变化和投资含义。

二、重点事件
请列出 3-5 条重点事件。每条按以下格式：
1. 事件标题：
摘要：
情绪方向：正面 / 中性 / 负面
影响程度：高 / 中 / 低
相关主体：
投资含义：
风险提示：

三、需要人工确认的信息
列出需要人工进一步核实的数据、公告、新闻来源或市场信息。

四、是否建议进入深度报告
回答：建议 / 暂不建议，并说明原因。

注意：
- 不得编造具体新闻、公告、数据。
- 如果没有给出具体材料，请基于用户给出的主题说明“需要补充新闻来源或材料”。
"""
    elif task_type == "深度报告":
        user_prompt = f"""
请基于以下需求，生成一份金融投研深度报告初稿框架：

用户需求：
{user_text}

请按以下格式输出：

【深度报告初稿】

一、核心结论

二、近期事件与舆情

三、行业与公司分析

四、基本面变化

五、估值与市场预期

六、投资建议
必须标注：需人工确认。

七、风险提示

八、需要补充的数据和材料

注意：
- 不得编造数据。
- 信息不足时必须写“信息不足”。
"""
    elif task_type == "研报摘要":
        user_prompt = f"""
请对以下内容做研报摘要或资料提炼：

用户需求：
{user_text}

请按以下格式输出：

【研报/资料摘要】

一、核心结论

二、投资逻辑

三、关键数据或事实

四、风险提示

五、可跟踪指标

六、需要人工确认的信息
"""
    else:
        user_prompt = user_text

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 1,
    }

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()

        print("DeepSeek result:", data)

        if "choices" not in data:
            error_msg = data.get("error", {}).get("message", str(data))
            return f"DeepSeek 调用失败：{error_msg}"

        return data["choices"][0]["message"]["content"]

    except Exception as e:
        print("DeepSeek error:", repr(e))
        return f"调用 DeepSeek 失败：{repr(e)}"



async def call_kimi(user_text: str, task_type: str) -> str:
    if not KIMI_API_KEY:
        return "Kimi API Key 还没有配置，请先在服务器 .env 里配置 KIMI_API_KEY。"

    url = f"{KIMI_BASE_URL}/chat/completions"

    system_prompt = """
你是一个资深金融研究员，擅长深度报告撰写、长文档整理、研报摘要和投研逻辑梳理。

要求：
- 必须用中文回答。
- 不得编造数据。
- 信息不足时必须明确写“信息不足”。
- 必须区分事实、判断和假设。
- 涉及投资建议时，必须标注“需人工确认”。
- 语言风格正式，适合金融投研报告。
"""

    if task_type == "投研日报":
        system_prompt = """
你是基金公司/资管机构的投研日报编辑。

请围绕以下结构整理日报：
一、A股市场
二、海外市场
三、汇率
四、商品
五、中国宏观
六、重要舆情
七、政策与事件
八、当日关注

要求：
- 不生成公司/主体影响分析、估值与市场预期、投资建议或深度报告式风险提示。
- 不反复输出“资料不足”或“需要人工确认”。
- 缺少数据时统一简写为“暂无有效数据。”
- 不得虚构资料中不存在的事实。
- 语言正式、简洁，适合晨会和投研日报。
"""
        user_prompt = user_text

    elif task_type == "深度报告":
        user_prompt = f"""
请根据以下需求，撰写一份正式的深度报告初稿，总字数控制在1500字以内。

用户需求：
{user_text}

请严格按以下结构输出，内容精炼，不要过度展开：

【深度报告】

一、核心结论
用 3-5 条概括核心判断，并标注哪些结论需要人工确认。

二、近期事件与舆情
梳理近期相关事件、政策、公告、市场关注点。
如果缺少具体材料，请明确说明“信息不足”。

三、行业与产业链分析
分析产业链环节、竞争格局、供需变化、技术路线、商业化进度。

四、公司/主体影响分析
如果用户提到具体公司或主体，请分别分析影响。
如果没有具体公司，请写行业层面影响。

五、基本面变化
分析收入、利润、订单、成本、产能、现金流等可能变化。
不得编造具体财务数据。

六、估值与市场预期
分析市场预期、估值弹性、预期差来源。
不得给出未经验证的具体估值结论。

七、投资建议
给出观察方向和研究建议。
必须标注：需人工确认。

八、风险提示
列出政策、需求、技术、竞争、估值、数据真实性等风险。

九、需要补充的数据和材料
列出后续需要补充的公告、研报、新闻、财务数据、市场数据。
"""
    elif task_type == "研报摘要":
        user_prompt = f"""
请对以下内容做投研资料整理或研报摘要。

用户需求：
{user_text}

请严格按以下结构输出，内容精炼，不要过度展开：

【研报/资料摘要】

一、核心结论

二、投资逻辑

三、关键事实与数据
如果用户没有提供原文或数据，请写“信息不足”。

四、产业链或公司影响

五、风险提示

六、可跟踪指标

七、需要人工确认的信息
"""
    else:
        user_prompt = f"""
请作为金融投研助手，基于以下需求进行分析：

{user_text}

要求：
- 结构清晰。
- 不得编造事实。
- 信息不足时明确说明。
- 涉及投资判断时提示需人工确认。
"""

    payload = {
        "model": KIMI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 1,
    }

    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()

        print("Kimi result:", data)

        if "choices" not in data:
            error_msg = data.get("error", {}).get("message", str(data))
            return f"Kimi 调用失败：{error_msg}"

        return data["choices"][0]["message"]["content"]

    except Exception as e:
        print("Kimi error:", repr(e))
        return f"调用 Kimi 失败：{repr(e)}"


async def call_deepseek_for_news_json(user_text: str, model_answer: str) -> dict:
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"

    prompt = f"""
请把以下舆情分析内容整理成严格 JSON，用于写入飞书多维表格。

用户原始需求：
{user_text}

模型分析内容：
{model_answer}

只输出 JSON，不要输出解释文字。

JSON 字段如下：
{{
  "主题": "",
  "行业": "",
  "公司/主体": "",
  "标题": "",
  "摘要": "",
  "情绪方向": "正面/中性/负面",
  "影响程度": "高/中/低",
  "投资含义": "",
  "风险提示": ""
}}

规则：
- 如果信息不足，字段填写“信息不足”。
- 情绪方向只能是：正面、中性、负面。
- 影响程度只能是：高、中、低。
- 不要编造具体新闻事实。
"""

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是金融投研数据结构化助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(url, headers=headers, json=payload)
        data = resp.json()

    print("DeepSeek JSON result:", data)

    if "choices" not in data:
        return {}

    content = data["choices"][0]["message"]["content"]
    return extract_json_from_text(content)



def split_text_for_doc(text: str, max_len: int = 1800):
    lines = text.splitlines()
    chunks = []
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            if current.strip():
                chunks.append(current.strip())
            current = line
        else:
            current += ("\n" if current else "") + line

    if current.strip():
        chunks.append(current.strip())

    return chunks


async def create_feishu_doc(title: str, content: str) -> str:
    """
    创建飞书新版文档，并把报告内容写入文档。
    返回文档 URL。
    """
    token = await get_tenant_access_token()

    create_url = "https://open.feishu.cn/open-apis/docx/v1/documents"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    payload = {
        "title": title
    }

    if FEISHU_DOC_FOLDER_TOKEN:
        payload["folder_token"] = FEISHU_DOC_FOLDER_TOKEN

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(create_url, headers=headers, json=payload)
        data = resp.json()

    print("Create doc result:", data)

    if data.get("code") != 0:
        raise RuntimeError(f"创建飞书文档失败: {data}")

    document = data.get("data", {}).get("document", {})
    document_id = document.get("document_id")
    document_url = document.get("url") or document.get("document_url")

    if not document_id:
        raise RuntimeError(f"创建飞书文档成功但未返回 document_id: {data}")

    # 飞书新版文档是块结构。通常可用 document_id 作为根 block_id 添加子块。
    chunks = split_text_for_doc(content)

    children = []
    for chunk in chunks:
        children.append({
            "block_type": 2,
            "text": {
                "elements": [
                    {
                        "text_run": {
                            "content": chunk
                        }
                    }
                ],
                "style": {}
            }
        })

    if children:
        add_url = (
            "https://open.feishu.cn/open-apis/docx/v1/documents/"
            f"{document_id}/blocks/{document_id}/children"
        )

        add_payload = {
            "children": children
        }

        async with httpx.AsyncClient(timeout=60) as client:
            add_resp = await client.post(add_url, headers=headers, json=add_payload)
            add_data = add_resp.json()

        print("Add doc blocks result:", add_data)

        if add_data.get("code") != 0:
            raise RuntimeError(f"写入飞书文档失败: {add_data}")

    if not document_url:
        document_url = f"https://{os.getenv('FEISHU_TENANT_DOMAIN', 'qcn787gcsi1s.feishu.cn')}/docx/{document_id}"

    return document_url


async def write_task_record(task_id: str, task_type: str, user_text: str, status: str, result_summary: str = "", error_msg: str = ""):
    if not FEISHU_TASK_TABLE_ID:
        print("未配置 FEISHU_TASK_TABLE_ID，跳过任务表写入")
        return

    t = now_ms()

    fields = {
        "任务ID": task_id,
        "任务类型": task_type,
        "原始指令": user_text,
        "任务状态": status,
        "创建时间": t,
        "完成时间": t if status in ["已完成", "失败"] else None,
        "结果摘要": result_summary[:1000] if result_summary else "",
        "错误信息": error_msg,
    }

    await create_bitable_record(FEISHU_TASK_TABLE_ID, fields)


async def write_news_record(user_text: str, model_answer: str):
    if not FEISHU_NEWS_TABLE_ID:
        print("未配置 FEISHU_NEWS_TABLE_ID，跳过舆情池写入")
        return

    news_json = await call_deepseek_for_news_json(user_text, model_answer)

    if isinstance(news_json, list):
        news_json = news_json[0] if news_json and isinstance(news_json[0], dict) else {}

    if not isinstance(news_json, dict):
        news_json = {}

    t = now_ms()

    fields = {
        "日期": t,
        "主题": news_json.get("主题", "信息不足"),
        "行业": news_json.get("行业", "信息不足"),
        "公司/主体": news_json.get("公司/主体", "信息不足"),
        "标题": news_json.get("标题", "信息不足"),
        "摘要": news_json.get("摘要", "信息不足"),
        "情绪方向": news_json.get("情绪方向", "中性"),
        "影响程度": news_json.get("影响程度", "中"),
        "投资含义": news_json.get("投资含义", "信息不足"),
        "风险提示": news_json.get("风险提示", "信息不足"),
        "人工确认状态": "待确认",
        "是否生成报告": False,
    }

    await create_bitable_record(FEISHU_NEWS_TABLE_ID, fields)




def clean_json_from_ai(text: str) -> dict:
    """
    尽量从 AI 返回中提取 JSON。
    兼容 ```json ... ``` 或前后带解释文字的情况。
    """
    if not text:
        return {}

    raw = text.strip()

    raw = raw.replace("```json", "").replace("```JSON", "").replace("```", "").strip()

    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        raw = match.group(0)

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
    except Exception as e:
        print("结构化JSON解析失败:", repr(e), "raw:", raw[:500])

    return {}


async def extract_report_fields(user_text: str, report_text: str) -> dict:
    """
    从深度报告正文中提取报告库字段。
    用于让报告库字段更干净，而不是把整段报告塞进核心结论。
    """
    prompt = f"""
请从下面这份金融投研深度报告中提取结构化字段。

用户原始指令：
{user_text}

报告正文：
{report_text[:12000]}

请只返回 JSON，不要返回任何解释文字。

JSON 格式如下：
{{
  "报告标题": "不超过40字",
  "报告类型": "深度报告",
  "行业": "如果无法判断，写信息不足",
  "公司/主体": "如果无法判断，写信息不足",
  "核心结论": "用3-5条提炼，控制在800字以内",
  "投资建议": "如果没有明确投资建议，写需人工确认",
  "风险提示": "用3-5条提炼，控制在800字以内"
}}

要求：
1. 不得编造报告正文中没有的信息。
2. 信息不足时写“信息不足”。
3. 投资建议必须谨慎，不能直接给确定性买卖建议。
4. 如果原文没有明确建议，投资建议写“需人工确认”。
"""

    try:
        result = await call_deepseek(prompt, "报告字段提取")
        data = clean_json_from_ai(result)
    except Exception as e:
        print("提取报告字段失败:", repr(e))
        data = {}

    return data


async def write_report_record(user_text: str, report_text: str, doc_url: str):
    if not FEISHU_REPORT_TABLE_ID:
        print("未配置 FEISHU_REPORT_TABLE_ID，跳过报告库写入")
        return

    structured = await extract_report_fields(user_text, report_text)

    title = structured.get("报告标题") or ("AI深度报告-" + datetime.now().strftime("%Y%m%d-%H%M"))
    report_type = structured.get("报告类型") or "深度报告"
    industry = structured.get("行业") or "信息不足"
    subject = structured.get("公司/主体") or "信息不足"
    conclusion = structured.get("核心结论") or report_text[:1000]
    investment_advice = structured.get("投资建议") or "需人工确认"
    risk = structured.get("风险提示") or "需人工确认"

    fields = {
        "报告标题": title[:100],
        "报告类型": report_type,
        "行业": industry[:100],
        "公司/主体": subject[:100],
        "报告日期": now_ms(),
        "核心结论": conclusion[:2000],
        "投资建议": investment_advice[:2000],
        "风险提示": risk[:2000],
        "飞书文档链接": {
            "text": "查看报告",
            "link": doc_url
        },
        "审核状态": "草稿",
    }

    await create_bitable_record(FEISHU_REPORT_TABLE_ID, fields)


def extract_text_from_pdf(file_path: str, max_chars: int = 20000) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        parts = []

        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                parts.append(page_text.strip())

            if sum(len(x) for x in parts) > max_chars:
                break

        return "\n\n".join(parts)[:max_chars]
    except Exception as e:
        print("PDF解析失败:", repr(e))
        return ""


def extract_text_from_docx(file_path: str, max_chars: int = 20000) -> str:
    try:
        from docx import Document

        doc = Document(file_path)
        parts = []

        for p in doc.paragraphs:
            if p.text and p.text.strip():
                parts.append(p.text.strip())

            if sum(len(x) for x in parts) > max_chars:
                break

        return "\n\n".join(parts)[:max_chars]
    except Exception as e:
        print("Word解析失败:", repr(e))
        return ""


def extract_text_from_file(file_path: str, max_chars: int = 20000) -> str:
    suffix = Path(file_path).suffix.lower()

    if suffix == ".pdf":
        return extract_text_from_pdf(file_path, max_chars=max_chars)

    if suffix == ".docx":
        return extract_text_from_docx(file_path, max_chars=max_chars)

    if suffix in [".txt", ".md"]:
        try:
            return Path(file_path).read_text(encoding="utf-8", errors="ignore")[:max_chars]
        except Exception:
            return ""

    return ""


async def summarize_file_with_kimi(file_name: str, file_text: str) -> str:
    if not file_text.strip():
        return "文件正文提取失败或内容为空。可能原因：PDF 是扫描件、文件加密、格式暂不支持，或文件内容无法直接提取。"

    prompt = f"""
请阅读以下研报/资料正文，并做金融投研摘要。

文件名：
{file_name}

正文：
{file_text}

请严格按以下结构输出：

【研报/资料摘要】

一、核心结论
用 3-5 条总结核心观点。

二、投资逻辑
说明主要驱动因素、产业链逻辑、公司/主体影响。

三、关键事实与数据
只列正文中明确出现的数据和事实。不得编造。

四、风险提示
列出需求、政策、竞争、估值、财务、数据真实性等风险。

五、可跟踪指标
列出后续应跟踪的指标、公告、新闻或数据。

六、需要人工确认的信息
列出需要人工核实的内容。

要求：
- 不得编造正文中没有的数据。
- 信息不足时写“信息不足”。
- 涉及投资建议时标注“需人工确认”。
"""

    return await call_kimi(prompt, "研报摘要")


def guess_material_type(user_text: str) -> str:
    if "研报" in user_text:
        return "研报"
    if "公告" in user_text:
        return "公告"
    if "新闻" in user_text or "舆情" in user_text:
        return "新闻"
    if "纪要" in user_text or "会议" in user_text:
        return "会议纪要"
    if "观点" in user_text:
        return "自己观点"
    return "其他"


def short_title_from_text(user_text: str) -> str:
    title = user_text.replace("\n", " ").strip()
    title = title[:40] if title else "AI知识库素材"
    return title



def extract_keywords_for_knowledge(user_text: str) -> list:
    """
    从用户指令中粗略提取知识库检索关键词。
    先用简单规则，后续可升级成 DeepSeek 结构化提取。
    """
    candidates = [
        "机器人", "减速器", "控制器", "传感器", "整机厂",
        "AI", "人工智能", "算力", "半导体", "芯片",
        "光伏", "储能", "新能源", "电池", "锂电",
        "医药", "创新药", "CXO",
        "地产", "城投", "债券", "ABS",
        "消费", "白酒", "汽车", "出口"
    ]

    keywords = [k for k in candidates if k in user_text]

    # 如果没有命中特定关键词，就用用户指令前几个词做兜底
    if not keywords:
        cleaned = user_text.replace("@投研助手", "").replace("基于已有知识库", "").replace("写一篇", "")
        keywords = [cleaned[:20]]

    return keywords


async def read_knowledge_records(limit: int = 10, user_text: str = "") -> str:
    """
    读取飞书多维表格“知识库素材”记录，
    并按用户指令关键词做简单筛选，用于生成深度报告。
    """
    if not FEISHU_KNOWLEDGE_TABLE_ID:
        print("未配置 FEISHU_KNOWLEDGE_TABLE_ID，跳过读取知识库素材")
        return ""

    token = await get_tenant_access_token()

    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_KNOWLEDGE_TABLE_ID}/records"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    params = {
        "page_size": 50
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers, params=params)

    data = resp.json()
    print("Read knowledge result:", data)

    if data.get("code") != 0:
        print("读取知识库素材失败:", data)
        return ""

    items = data.get("data", {}).get("items", [])

    if not items:
        return ""

    keywords = extract_keywords_for_knowledge(user_text) if user_text else []
    print("Knowledge search keywords:", keywords)

    filtered_items = []

    for item in items:
        fields = item.get("fields", {})

        searchable = "\n".join([
            str(fields.get("素材标题", "")),
            str(fields.get("行业", "")),
            str(fields.get("公司/主体", "")),
            str(fields.get("摘要", "")),
            str(fields.get("核心结论", "")),
            str(fields.get("投资逻辑", "")),
            str(fields.get("原始内容", "")),
        ])

        if not keywords or any(k and k in searchable for k in keywords):
            filtered_items.append(item)

    # 如果筛选后为空，退回使用最近几条，避免完全没有参考资料
    if not filtered_items:
        print("No matched knowledge records, fallback to recent records")
        filtered_items = items[:limit]
    else:
        filtered_items = filtered_items[:limit]

    parts = []

    for i, item in enumerate(filtered_items, start=1):
        fields = item.get("fields", {})

        title = fields.get("素材标题", "")
        material_type = fields.get("素材类型", "")
        summary = fields.get("摘要", "")
        conclusion = fields.get("核心结论", "")
        logic = fields.get("投资逻辑", "")
        risk = fields.get("风险提示", "")
        indicators = fields.get("可跟踪指标", "")

        part = f"""
【知识库素材 {i}】
素材标题：{title}
素材类型：{material_type}

摘要：
{summary}

核心结论：
{conclusion}

投资逻辑：
{logic}

风险提示：
{risk}

可跟踪指标：
{indicators}
"""
        parts.append(part)

    return "\n\n".join(parts)[:20000]


async def write_knowledge_record(user_text: str, summary_text: str):
    if not FEISHU_KNOWLEDGE_TABLE_ID:
        print("未配置 FEISHU_KNOWLEDGE_TABLE_ID，跳过知识库素材写入")
        return

    t = now_ms()

    fields = {
        "素材标题": short_title_from_text(user_text),
        "素材类型": guess_material_type(user_text),
        "行业": "信息不足",
        "公司/主体": "信息不足",
        "摘要": summary_text[:1000] if summary_text else "",
        "核心结论": summary_text[:1000] if summary_text else "",
        "投资逻辑": summary_text[:1000] if summary_text else "",
        "风险提示": summary_text[:1000] if summary_text else "",
        "可跟踪指标": summary_text[:1000] if summary_text else "",
        "原始内容": user_text[:2000] if user_text else "",
        "上传时间": t,
        "是否可用于报告": True,
    }

    await create_bitable_record(FEISHU_KNOWLEDGE_TABLE_ID, fields)



def extract_query_keyword(user_text: str) -> str:
    keyword = user_text
    for x in [
        "@投研助手",
        "查一下",
        "查询",
        "查",
        "知识库",
        "素材",
        "报告库",
        "报告",
        "相关",
        "有哪些",
    ]:
        keyword = keyword.replace(x, "")
    keyword = keyword.strip()
    return keyword or user_text.strip()


def field_to_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "；".join(str(x) for x in value)
    if isinstance(value, dict):
        if "text" in value:
            return str(value.get("text", ""))
        if "link" in value:
            return str(value.get("link", ""))
        return str(value)
    return str(value)


async def query_bitable_records(table_id: str, keyword: str, limit: int = 20) -> list:
    token = await get_tenant_access_token()

    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{table_id}/records"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    params = {
        "page_size": 100
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers, params=params)

    data = resp.json()
    print("Query bitable result:", data)

    if data.get("code") != 0:
        print("查询多维表格失败:", data)
        return []

    items = data.get("data", {}).get("items", [])
    matched = []

    for item in items:
        fields = item.get("fields", {})
        searchable = "\\n".join(field_to_text(v) for v in fields.values())

        if not keyword or keyword in searchable:
            matched.append(fields)

    return matched[:limit]


async def handle_query_knowledge(user_text: str) -> str:
    keyword = extract_query_keyword(user_text)

    if not FEISHU_KNOWLEDGE_TABLE_ID:
        return "未配置知识库素材表 FEISHU_KNOWLEDGE_TABLE_ID。"

    records = await query_bitable_records(FEISHU_KNOWLEDGE_TABLE_ID, keyword, limit=10)

    if not records:
        return f"没有在知识库素材中找到与「{keyword}」相关的记录。"

    parts = [f"【知识库查询结果】关键词：{keyword}\\n"]

    for i, fields in enumerate(records, start=1):
        title = field_to_text(fields.get("素材标题"))
        material_type = field_to_text(fields.get("素材类型"))
        industry = field_to_text(fields.get("行业"))
        subject = field_to_text(fields.get("公司/主体"))
        summary = field_to_text(fields.get("摘要"))[:300]

        parts.append(
            f"{i}. {title}\\n"
            f"类型：{material_type}\\n"
            f"行业：{industry}\\n"
            f"主体：{subject}\\n"
            f"摘要：{summary}\\n"
        )

    return "\\n".join(parts)


async def handle_query_report(user_text: str) -> str:
    keyword = extract_query_keyword(user_text)

    if not FEISHU_REPORT_TABLE_ID:
        return "未配置报告库 FEISHU_REPORT_TABLE_ID。"

    records = await query_bitable_records(FEISHU_REPORT_TABLE_ID, keyword, limit=10)

    if not records:
        return f"没有在报告库中找到与「{keyword}」相关的记录。"

    parts = [f"【报告库查询结果】关键词：{keyword}\\n"]

    for i, fields in enumerate(records, start=1):
        title = field_to_text(fields.get("报告标题"))
        report_type = field_to_text(fields.get("报告类型"))
        industry = field_to_text(fields.get("行业"))
        subject = field_to_text(fields.get("公司/主体"))
        conclusion = field_to_text(fields.get("核心结论"))[:300]

        doc_link = fields.get("飞书文档链接")
        if isinstance(doc_link, dict):
            link = doc_link.get("link", "")
        else:
            link = field_to_text(doc_link)

        parts.append(
            f"{i}. {title}\\n"
            f"类型：{report_type}\\n"
            f"行业：{industry}\\n"
            f"主体：{subject}\\n"
            f"核心结论：{conclusion}\\n"
            f"文档链接：{link}\\n"
        )

    return "\\n".join(parts)



async def read_recent_table_records(table_id: str, limit: int = 10) -> list:
    """
    读取指定多维表格最近若干条记录。
    """
    token = await get_tenant_access_token()

    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{table_id}/records"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    params = {
        "page_size": limit
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers, params=params)

    data = resp.json()
    print("Read recent table records result:", data)

    if data.get("code") != 0:
        print("读取多维表格失败:", data)
        return []

    return data.get("data", {}).get("items", [])


def format_records_for_daily(title: str, items: list) -> str:
    if not items:
        return f"【{title}】\\n暂无记录。"

    parts = [f"【{title}】"]

    for i, item in enumerate(items, start=1):
        fields = item.get("fields", {})
        lines = []

        for k, v in fields.items():
            value = field_to_text(v)
            if value:
                lines.append(f"{k}：{value[:300]}")

        parts.append(f"\\n{i}. " + "\\n".join(lines[:8]))

    return "\\n".join(parts)

def format_market_records_for_daily(items: list) -> str:
    """
    将市场数据按 A股、海外市场、汇率、商品、中国宏观分类展示。
    """
    if not items:
        return "【最近市场数据】\n暂无记录。"

    group_order = [
        ("一、A股市场", [
            "上证指数",
            "深证成指",
            "创业板指",
            "沪深300",
            "中证500",
        ]),
        ("二、海外市场", [
            "恒生指数",
            "纳斯达克",
        ]),
        ("三、汇率", [
            "美元兑人民币",
            "美元指数",
        ]),
        ("四、商品", [
            "布伦特原油",
            "伦敦金",
        ]),
        ("五、中国宏观", [
            "CPI同比",
            "PPI同比",
            "制造业PMI",
        ]),
    ]

    records_by_name = {}

    for item in items:
        fields = item.get("fields", {})
        name = field_to_text(fields.get("指标名称"))

        if not name:
            continue

        records_by_name[name] = fields

    parts = ["【最近市场数据】"]

    for group_title, indicator_names in group_order:
        lines = []

        for name in indicator_names:
            fields = records_by_name.get(name)

            if not fields:
                continue

            value = field_to_text(fields.get("数值"))
            change = field_to_text(fields.get("涨跌幅"))
            unit = field_to_text(fields.get("单位"))

            value_text = value or "--"

            if unit and value_text != "--":
                value_text = f"{value_text}{unit}"

            line = f"- {name}：{value_text}"

            if change:
                change_text = change
                if not change_text.endswith("%"):
                    change_text = f"{change_text}%"
                line += f"（涨跌幅 {change_text}）"

            lines.append(line)

        if lines:
            parts.append(f"\n{group_title}")
            parts.extend(lines)

    if len(parts) == 1:
        return "【最近市场数据】\n暂无可识别的市场数据。"

    return "\n".join(parts)

async def generate_daily_report(user_text: str) -> str:
    """
    生成投研日报正文。
    """
    market_items = []
    news_items = []
    knowledge_items = []
    report_items = []

    if FEISHU_MARKET_TABLE_ID:
        market_items = await read_recent_table_records(FEISHU_MARKET_TABLE_ID, limit=30)

    if FEISHU_NEWS_TABLE_ID:
        news_items = await read_recent_table_records(FEISHU_NEWS_TABLE_ID, limit=10)

    if FEISHU_KNOWLEDGE_TABLE_ID:
        knowledge_items = await read_recent_table_records(FEISHU_KNOWLEDGE_TABLE_ID, limit=10)

    if FEISHU_REPORT_TABLE_ID:
        report_items = await read_recent_table_records(FEISHU_REPORT_TABLE_ID, limit=5)

    market_text = format_market_records_for_daily(market_items)
    news_text = format_records_for_daily("最近舆情池记录", news_items)
    knowledge_text = format_records_for_daily("最近知识库素材", knowledge_items)
    report_text = format_records_for_daily("最近报告库记录", report_items)

    prompt = f"""
请基于以下飞书多维表格资料，生成一份正式的《投研日报》。

用户指令：
{user_text}

资料一：市场数据表
{market_text}

资料二：舆情池
{news_text}

资料三：知识库素材
{knowledge_text}

资料四：报告库
{report_text}

请严格按照以下结构输出：

【投研日报】

一、今日核心结论
用3-5条总结今天最值得关注的投研信息。

二、重点舆情与事件
按行业/主题归纳重点事件，并说明可能影响。

三、知识库新增资料摘要
总结最近沉淀的研报、资料或纪要。

四、值得进一步跟踪的方向
列出后续需要关注的行业、公司/主体、指标或事件。

五、风险提示
列出市场、政策、行业、公司、数据真实性等风险。

六、需要人工确认的信息
列出信息不足、需要人工核验的地方。

要求：
1. 不得编造资料中没有的数据。
2. 信息不足时写“信息不足”。
3. 投资判断必须写“需人工确认”。
4. 语言正式，适合作为投研日报初稿。
"""

    return await call_kimi(prompt, "投研日报")


async def handle_daily_report(user_text: str) -> str:
    report_text = await generate_daily_report(user_text)

    title = "投研日报-" + datetime.now().strftime("%Y%m%d-%H%M")

    try:
        doc_url = await create_feishu_doc(title, report_text)
        await write_report_record(user_text, report_text, doc_url)

        return (
            report_text
            + f"\\n\\n【系统提示】已创建飞书文档：{doc_url}"
            + "\\n【系统提示】本次投研日报已写入报告库，状态：草稿。"
        )
    except Exception as e:
        print("生成投研日报归档失败:", repr(e))
        return report_text + f"\\n\\n【系统提示】生成投研日报归档失败：{repr(e)}"


@app.post("/feishu/events")
async def feishu_events(request: Request):
    body = await request.json()

    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body.get("challenge")})

    print("Received Feishu event:", body)

    message_id = None

    try:
        event = body.get("event", {})
        message = event.get("message", {})
        message_id = message.get("message_id")
        message_type = message.get("message_type")

        # 先识别文件消息：第一阶段只打印 file_key，方便后续下载
        if message_id and message_type in ["file", "media"]:
            file_info = extract_file_info_from_message(message)
            print("Received Feishu file message:", file_info)

            file_key = file_info.get("file_key")
            file_name = file_info.get("file_name") or "feishu_file"

            if not file_key:
                await reply_feishu_message(
                    message_id,
                    "我收到了文件消息，但没有识别到 file_key，暂时无法下载。"
                )
                return {"code": 0, "msg": "file key missing"}

            try:
                local_path = await download_feishu_message_file(message_id, file_key, file_name)

                file_text = extract_text_from_file(local_path, max_chars=20000)

                if not file_text.strip():
                    await reply_feishu_message(
                        message_id,
                        "我已下载文件，但没有成功提取正文。\n"
                        f"文件名：{file_name}\n"
                        f"保存路径：{local_path}\n\n"
                        "可能原因：PDF 是扫描件、文件加密、格式暂不支持，或文件内容无法直接提取。"
                    )
                    return {"code": 0, "msg": "file downloaded but no text"}

                summary_text = await summarize_file_with_kimi(file_name, file_text)

                try:
                    await write_knowledge_record(
                        f"文件名：{file_name}\n\n正文节选：\n{file_text[:2000]}",
                        summary_text
                    )
                    system_tip = "\n\n【系统提示】本次文件摘要已写入飞书多维表格“知识库素材”，可用于后续报告。"
                except Exception as e:
                    print("写入知识库素材失败:", repr(e))
                    system_tip = f"\n\n【系统提示】写入知识库素材失败：{repr(e)}"

                await reply_feishu_message(
                    message_id,
                    summary_text + system_tip
                )
            except Exception as e:
                print("下载飞书文件失败:", repr(e))
                await reply_feishu_message(
                    message_id,
                    f"我收到了文件，但下载失败：{repr(e)}"
                )

            return {"code": 0, "msg": "file processed"}

        # 只处理文本消息
        if not message_id or message_type != "text":
            print("Unsupported message type:", message_type, message)
            return {"code": 0, "msg": "ok"}

        # 已完整处理过的消息，直接忽略
        if message_id in PROCESSED_MESSAGE_IDS:
            print("Duplicate completed message ignored:", message_id)
            return {"code": 0, "msg": "duplicate completed ignored"}

        # 正在处理中的消息，说明是飞书重试，先忽略，避免重复生成
        if message_id in PROCESSING_MESSAGE_IDS:
            print("Duplicate processing message ignored:", message_id)
            return {"code": 0, "msg": "duplicate processing ignored"}

        PROCESSING_MESSAGE_IDS.add(message_id)

        content_raw = message.get("content", "{}")
        content = json.loads(content_raw)
        user_text = clean_feishu_mention(content.get("text", ""))

        if not user_text:
            reply_text = "我收到了，但没有识别到具体问题。你可以说：帮我梳理今天机器人产业链舆情。"
            await reply_feishu_message(message_id, reply_text)
            PROCESSED_MESSAGE_IDS.add(message_id)
            return {"code": 0, "msg": "ok"}

        task_id = str(uuid.uuid4())[:8]
        task_type = detect_task_type(user_text)

        await write_task_record(task_id, task_type, user_text, "处理中")

        normalized_user_text = user_text.strip()

        daily_report_commands = {
            "投研日报",
            "生成投研日报",
            "生成日报",
            "今日投研日报",
        }

        if normalized_user_text in daily_report_commands:
            reply_text = await handle_daily_report(normalized_user_text)

        elif task_type == "深度报告":
            knowledge_text = await read_knowledge_records(limit=10, user_text=user_text)

            if knowledge_text:
                enhanced_prompt = f"""
用户原始指令：
{user_text}

以下是飞书多维表格“知识库素材”中沉淀的历史研报、资料、纪要和摘要，请优先参考，但不要机械照搬。

【知识库参考资料】
{knowledge_text}

请基于用户指令和知识库参考资料，生成一篇正式的金融投研深度报告。

要求：
1. 优先使用知识库中已有事实和逻辑。
2. 不得编造知识库和用户指令中没有的数据。
3. 对无法确认的信息，必须写“信息不足”或“需人工确认”。
4. 报告结构应包括：核心结论、行业背景、产业链分析、公司/主体分析、投资逻辑、风险提示、后续跟踪指标。
5. 语言正式，适合作为投研初稿。
"""
                reply_text = await call_kimi(enhanced_prompt, task_type)
                reply_text = reply_text + "\n\n【系统提示】本次深度报告已参考飞书多维表格“知识库素材”中的历史资料。"
            else:
                reply_text = await call_kimi(user_text, task_type)

        elif task_type == "研报摘要":
            reply_text = await call_kimi(user_text, task_type)

        else:
            reply_text = await call_deepseek(user_text, task_type)

        if task_type == "舆情梳理":
            try:
                await write_news_record(user_text, reply_text)
                reply_text = reply_text + "\n\n【系统提示】本次舆情分析已写入飞书多维表格“舆情池”，状态：待确认。"
            except Exception as e:
                print("写入舆情池失败:", repr(e))
                reply_text = reply_text + f"\n\n【系统提示】写入舆情池失败：{repr(e)}"

        if task_type == "深度报告":
            try:
                title = "AI深度报告-" + datetime.now().strftime("%Y%m%d-%H%M")
                doc_url = await create_feishu_doc(title, reply_text)
                await write_report_record(user_text, reply_text, doc_url)
                reply_text = reply_text + f"\n\n【系统提示】已创建飞书文档：{doc_url}\n【系统提示】本次深度报告已写入飞书多维表格“报告库”，状态：草稿。"
            except Exception as e:
                print("创建飞书文档或写入报告库失败:", repr(e))
                reply_text = reply_text + f"\n\n【系统提示】创建飞书文档或写入报告库失败：{repr(e)}"

        if task_type == "研报摘要":
            try:
                await write_knowledge_record(user_text, reply_text)
                reply_text = reply_text + "\n\n【系统提示】本次资料整理已写入飞书多维表格“知识库素材”，可用于后续报告。"
            except Exception as e:
                print("写入知识库素材失败:", repr(e))
                reply_text = reply_text + f"\n\n【系统提示】写入知识库素材失败：{repr(e)}"

        await write_task_record(task_id, task_type, user_text, "已完成", reply_text)

        await reply_feishu_message(message_id, reply_text)

        # 只有完整走完后，才标记为已完成
        PROCESSED_MESSAGE_IDS.add(message_id)

        if len(PROCESSED_MESSAGE_IDS) > 1000:
            PROCESSED_MESSAGE_IDS.clear()

    except Exception as e:
        print("处理飞书事件失败:", repr(e))

    finally:
        if message_id:
            PROCESSING_MESSAGE_IDS.discard(message_id)

    return {"code": 0, "msg": "ok"}

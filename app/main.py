from fastapi import Request
from fastapi.responses import JSONResponse
from datetime import datetime
import asyncio
import json
import re
import uuid
import httpx
import time
from pathlib import Path

from app.bootstrap import create_app, initialize_services
from app.config import load_config
from app.router.task_router import (
    DAILY_REPORT,
    FUND_ANALYSIS,
    GENERAL_CHAT,
    REPORT_ANALYSIS,
    RESEARCH_REPORT,
    SENTIMENT_ANALYSIS,
    route_task,
)
APP_CONFIG = load_config()
APP_SERVICES = initialize_services(APP_CONFIG)
FEISHU_ADAPTER = APP_SERVICES.feishu_adapter

# 去重：避免飞书重试导致同一条消息被处理多次
# PROCESSING_MESSAGE_IDS：正在处理中的消息
# PROCESSED_MESSAGE_IDS：已经完整处理成功的消息
PROCESSING_MESSAGE_IDS = set()
PROCESSED_MESSAGE_IDS = set()

TASK_TYPE_LABELS = {
    SENTIMENT_ANALYSIS: "舆情梳理",
    REPORT_ANALYSIS: "研报摘要",
    RESEARCH_REPORT: "深度报告",
    FUND_ANALYSIS: "基金产品研究",
    GENERAL_CHAT: "普通问答",
    DAILY_REPORT: "投研日报",
}


FEISHU_APP_ID = APP_CONFIG.feishu.app_id
FEISHU_APP_SECRET = APP_CONFIG.feishu.app_secret

DEEPSEEK_API_KEY = APP_CONFIG.deepseek.api_key
DEEPSEEK_BASE_URL = APP_CONFIG.deepseek.base_url

KIMI_API_KEY = APP_CONFIG.kimi.api_key
KIMI_BASE_URL = APP_CONFIG.kimi.base_url
KIMI_MODEL = APP_CONFIG.kimi.model

FEISHU_BITABLE_APP_TOKEN = APP_CONFIG.feishu.bitable_app_token
FEISHU_NEWS_TABLE_ID = APP_CONFIG.feishu.news_table_id
FEISHU_TASK_TABLE_ID = APP_CONFIG.feishu.task_table_id
FEISHU_REPORT_TABLE_ID = APP_CONFIG.feishu.report_table_id
FEISHU_KNOWLEDGE_TABLE_ID = APP_CONFIG.feishu.knowledge_table_id
FEISHU_MARKET_TABLE_ID = APP_CONFIG.feishu.market_table_id
FEISHU_DOC_FOLDER_TOKEN = APP_CONFIG.feishu.doc_folder_token
FEISHU_TENANT_DOMAIN = APP_CONFIG.feishu.tenant_domain


async def check_bitable_read_status(table_id: str | None) -> str:
    if not table_id or not FEISHU_BITABLE_APP_TOKEN:
        return "未配置"

    token = await get_tenant_access_token()
    url = (
        "https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{table_id}/records"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(url, headers=headers, params={"page_size": 1})
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError("FeishuAPIError")

    items = data.get("data", {}).get("items", [])
    return "可用" if items else "可用（暂无记录）"


async def handle_official_research_health() -> str:
    import asyncio
    from datetime import timedelta

    from app.providers.registry import (
        get_enabled_providers,
        official_research_enabled,
    )

    provider_labels = {
        "cninfo": "巨潮资讯 Provider",
        "miit": "工信部 Provider",
    }
    provider_statuses = {
        name: "未检查（功能关闭）" for name in provider_labels
    }

    try:
        enabled = official_research_enabled()
        feature_status = "开启" if enabled else "关闭"
    except Exception as exc:
        enabled = False
        feature_status = f"异常（{type(exc).__name__}）"

    if enabled:
        try:
            providers = {provider.name: provider for provider in get_enabled_providers()}
        except Exception as exc:
            providers = {}
            error_status = f"异常（{type(exc).__name__}）"
            provider_statuses = {name: error_status for name in provider_labels}
        else:
            now = datetime.now().astimezone()
            subject = {
                "query": "工业和信息化",
                "issuer": "平安银行",
                "stock_code": "000001",
            }
            for name in provider_labels:
                provider = providers.get(name)
                if provider is None:
                    provider_statuses[name] = "未启用"
                    continue
                try:
                    results = await asyncio.wait_for(
                        provider.search(
                            subject=subject,
                            since=now - timedelta(days=7),
                            until=now,
                            limit=1,
                        ),
                        timeout=5,
                    )
                    count = len(results)
                    provider_statuses[name] = (
                        f"可用（返回 {count} 条）" if count else "可用（暂无结果）"
                    )
                except Exception as exc:
                    provider_statuses[name] = f"异常（{type(exc).__name__}）"

    table_statuses = {}
    for label, table_id in (
        ("舆情池", FEISHU_NEWS_TABLE_ID),
        ("知识库", FEISHU_KNOWLEDGE_TABLE_ID),
        ("报告库", FEISHU_REPORT_TABLE_ID),
    ):
        try:
            table_statuses[label] = await check_bitable_read_status(table_id)
        except Exception as exc:
            table_statuses[label] = f"异常（{type(exc).__name__}）"

    checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    return (
        "【官方资料状态】\n"
        f"功能开关：{feature_status}\n"
        f"巨潮资讯 Provider：{provider_statuses['cninfo']}\n"
        f"工信部 Provider：{provider_statuses['miit']}\n"
        f"舆情池：{table_statuses['舆情池']}\n"
        f"知识库：{table_statuses['知识库']}\n"
        f"报告库：{table_statuses['报告库']}\n"
        f"检查时间：{checked_at}"
    )


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
    return TASK_TYPE_LABELS[route_task(user_text)]


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
你是基金公司或资管机构的固收研究员，负责生成适合固收晨会直接阅读的《固收投研日报》。

必须严格按照以下九个板块输出，标题不得改名、合并或省略：
一、今日固收核心结论
二、今日重点关注
三、资金面与流动性
四、利率债市场
五、信用债市场
六、可转债与跨资产表现
七、宏观与政策
八、重要舆情与机构观点
九、今日关注与风险提示

要求：
- 只输出一份完整日报，不按资料来源分别生成摘要，不重复输出同一事件或观点。
- “今日固收核心结论”先给出偏强、偏弱、震荡或分化等方向判断，再用3—5条说明驱动，并明确标注“市场事实”和“研究判断”。
- 每个重要结论尽量回答：发生了什么、为什么重要、对固收市场的影响、后续观察什么。
- 市场数据只作为量化事实；舆情池只作为事件线索；知识库只提供历史框架、制度背景和研究逻辑；报告库只提供已有研究观点和深度结论。
- 历史材料只能用于解释，不能覆盖更新的市场事实，也不得把报告库中的历史日报当作当日新增信息。
- 信息冲突时，优先采用日期更新者和市场事实；明确指出分歧及证据，不得把冲突观点同时写成最终结论。
- 对缺失数据必须明确说明，不得编造或推断。
- 不得输出“XX”“X.X”“X.XX”“XX亿元”“Xbp”“待确认”“人工确认后补充”“示例日期”等占位内容。
- 缺少资金利率数据时必须写“暂无新增资金面量化数据”，不得用股票、汇率或商品数据代替资金面指标。
- 缺少国债收益率、资金利率、信用利差等量化数据时必须如实说明，不得补造。
- 信用数据不足时可以依据舆情、知识库和报告库作定性判断，但必须明确标注“证据不足”。
- 股票、汇率和商品数据仅用于解释对固收资产的跨资产影响，不得占据日报主体。
- 没有可靠重点事件时写“暂无新增重点事件”，不得编造事实、新闻、政策、数据、日期或来源。
- 只做研究提示，不提供确定性收益承诺或未经验证的买卖建议。
- 语言正式、简洁，适合基金公司或资管机构固收晨会阅读。
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

    for attempt in range(2):
        try:
            timeout = httpx.Timeout(connect=15, read=180, write=30, pool=15)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()

            if "choices" not in data:
                error_msg = data.get("error", {}).get("message", "未知错误")
                return f"Kimi 调用失败：{error_msg}"

            return data["choices"][0]["message"]["content"]

        except httpx.TimeoutException:
            if attempt == 1:
                return "调用 Kimi 超时，请稍后重试"
            await asyncio.sleep(2)

        except Exception as exc:
            print(f"Kimi request failed: {type(exc).__name__}")
            return "调用 Kimi 失败，请稍后重试"


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
        document_url = f"https://{FEISHU_TENANT_DOMAIN}/docx/{document_id}"

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

    parts = [f"【知识库查询结果】关键词：{keyword}\n"]

    for i, fields in enumerate(records, start=1):
        title = field_to_text(fields.get("素材标题"))
        material_type = field_to_text(fields.get("素材类型"))
        industry = field_to_text(fields.get("行业"))
        subject = field_to_text(fields.get("公司/主体"))
        summary = field_to_text(fields.get("摘要"))[:300]

        parts.append(
            f"{i}. {title}\n"
            f"类型：{material_type}\n"
            f"行业：{industry}\n"
            f"主体：{subject}\n"
            f"摘要：{summary}\n"
        )

    return "\n".join(parts)


async def handle_query_report(user_text: str) -> str:
    keyword = extract_query_keyword(user_text)

    if not FEISHU_REPORT_TABLE_ID:
        return "未配置报告库 FEISHU_REPORT_TABLE_ID。"

    records = await query_bitable_records(FEISHU_REPORT_TABLE_ID, keyword, limit=10)

    if not records:
        return f"没有在报告库中找到与「{keyword}」相关的记录。"

    parts = [f"【报告库查询结果】关键词：{keyword}\n"]

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
            f"{i}. {title}\n"
            f"类型：{report_type}\n"
            f"行业：{industry}\n"
            f"主体：{subject}\n"
            f"核心结论：{conclusion}\n"
            f"文档链接：\n{link}\n"
        )

    return "\n".join(parts)



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


NEWS_DAILY_FIELDS = (
    "日期",
    "主题",
    "行业",
    "公司/主体",
    "标题",
    "摘要",
    "情绪方向",
    "影响程度",
    "投资含义",
    "风险提示",
)


def news_field_has_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def news_date_sort_key(value) -> tuple:
    if isinstance(value, dict):
        for key in ("timestamp", "value", "date", "text"):
            if key in value:
                return news_date_sort_key(value.get(key))
        return (0, 0.0)

    if isinstance(value, list):
        for item in value:
            parsed = news_date_sort_key(item)
            if parsed[0]:
                return parsed
        return (0, 0.0)

    if isinstance(value, bool) or value is None:
        return (0, 0.0)

    if isinstance(value, (int, float)):
        number = float(value)
        if not (-1e20 < number < 1e20):
            return (0, 0.0)
        while abs(number) > 10_000_000_000:
            number /= 1000
        return (1, number)

    text = str(value).strip()
    if not text:
        return (0, 0.0)

    normalized = text.replace("Z", "+00:00")
    try:
        return (1, datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        pass

    for date_format in ("%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return (1, datetime.strptime(text, date_format).timestamp())
        except ValueError:
            continue

    try:
        return news_date_sort_key(float(text))
    except ValueError:
        return (0, 0.0)


async def read_recent_news_records(limit: int = 10) -> list:
    if not FEISHU_NEWS_TABLE_ID or not FEISHU_BITABLE_APP_TOKEN or limit <= 0:
        return []

    token = await get_tenant_access_token()

    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_NEWS_TABLE_ID}/records"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers, params={"page_size": 100})

    data = resp.json()
    if data.get("code") != 0:
        print("读取舆情池失败:", data.get("code"), data.get("msg"))
        return []

    valid_items = []
    for item in data.get("data", {}).get("items", []):
        fields = item.get("fields")
        if not isinstance(fields, dict) or not fields:
            continue
        if not any(news_field_has_value(fields.get(name)) for name in NEWS_DAILY_FIELDS):
            continue
        valid_items.append(item)

    valid_items.sort(
        key=lambda item: news_date_sort_key(item.get("fields", {}).get("日期")),
        reverse=True,
    )
    return valid_items[:limit]


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


def format_news_records_for_daily(items: list) -> str:
    if not items:
        return "【最近舆情池记录】\n暂无记录。"

    output_fields = (
        "日期",
        "主题",
        "标题",
        "摘要",
        "情绪方向",
        "影响程度",
        "投资含义",
    )
    parts = ["【最近舆情池记录】"]

    for index, item in enumerate(items, start=1):
        fields = item.get("fields", {})
        lines = []
        for name in output_fields:
            value = field_to_text(fields.get(name)).strip()
            if value:
                lines.append(f"{name}：{value[:300]}")
        if lines:
            parts.append(f"\n{index}. " + "\n".join(lines))

    return "\n".join(parts)

def format_market_records_for_daily(items: list) -> str:
    """
    将市场数据按固收优先、跨资产辅助的方式分类展示。
    未识别但具有名称和有效数值的指标保留在“其他市场指标”中。
    """
    if not items:
        return "【最近市场数据】\n暂无记录。"

    fixed_income_keywords = (
        "国债", "政金债", "政金", "国开债", "农发债", "口行债", "国债期货",
        "dr007", "r007", "shibor", "资金利率", "回购利率", "信用利差",
        "等级利差", "期限利差", "收益率曲线", "中债", "同业存单",
        "票据利率", "城投债", "产业债", "可转债", "可交换债",
    )
    equity_keywords = (
        "上证指数", "深证成指", "创业板指", "沪深300", "中证500",
        "恒生指数", "纳斯达克", "标普500", "道琼斯",
    )
    fx_keywords = ("汇率", "美元兑", "人民币", "美元指数", "欧元", "日元")
    commodity_keywords = ("原油", "黄金", "伦敦金", "铜", "螺纹钢", "商品")
    macro_keywords = ("cpi", "ppi", "pmi", "社融", "m1", "m2", "工业增加值", "失业率")
    group_titles = (
        "固收与资金指标",
        "权益市场（跨资产参考）",
        "汇率（跨资产参考）",
        "商品（跨资产参考）",
        "宏观数据",
        "其他市场指标",
    )
    grouped_lines = {title: [] for title in group_titles}

    for item in items:
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            continue
        name = field_to_text(fields.get("指标名称")).strip()
        if not name:
            continue

        value = ""
        for field_name in ("数值", "最新值", "收盘价", "收益率", "利率", "利差"):
            value = field_to_text(fields.get(field_name)).strip()
            if value:
                break
        change = field_to_text(fields.get("涨跌幅")).strip()
        unit = field_to_text(fields.get("单位")).strip()
        if not value and not change:
            continue

        value_text = f"{value}{unit}" if value and unit else value
        line = f"- {name}"
        if value_text:
            line += f"：{value_text}"
        if change:
            change_text = change if change.endswith("%") else f"{change}%"
            line += f"（涨跌幅 {change_text}）"

        normalized_name = name.casefold()
        if any(keyword in normalized_name for keyword in fixed_income_keywords):
            group_title = "固收与资金指标"
        elif any(keyword.casefold() in normalized_name for keyword in equity_keywords):
            group_title = "权益市场（跨资产参考）"
        elif any(keyword in normalized_name for keyword in fx_keywords):
            group_title = "汇率（跨资产参考）"
        elif any(keyword in normalized_name for keyword in commodity_keywords):
            group_title = "商品（跨资产参考）"
        elif any(keyword in normalized_name for keyword in macro_keywords):
            group_title = "宏观数据"
        else:
            group_title = "其他市场指标"

        grouped_lines[group_title].append(line)

    parts = ["【最近市场数据】"]
    for group_title in group_titles:
        lines = grouped_lines[group_title]
        if lines:
            parts.append(f"\n【{group_title}】")
            parts.extend(lines)

    if len(parts) == 1:
        return "【最近市场数据】\n暂无可识别的市场数据。"

    return "\n".join(parts)


def filter_historical_daily_reports(items: list) -> list:
    """
    仅从本次日报的报告库上下文中排除明确属于历史日报的记录。
    不修改报告库原始记录，也保留其他深度报告。
    """
    daily_types = {"日报", "投研日报", "固收日报", "固收投研日报", "市场晨报"}
    daily_title_markers = ("投研日报", "固收日报", "市场晨报")
    daily_commands = {
        "投研日报",
        "生成投研日报",
        "生成日报",
        "今日投研日报",
        "固收投研日报",
        "生成固收日报",
        "今日固收日报",
    }
    filtered = []

    for item in items:
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            continue

        report_type = field_to_text(fields.get("报告类型")).strip()
        title = field_to_text(fields.get("报告标题")).strip()
        command = field_to_text(
            fields.get("原始指令") or fields.get("命令") or fields.get("用户指令")
        ).strip()
        is_daily = (
            report_type in daily_types
            or any(marker in title for marker in daily_title_markers)
            or command in daily_commands
        )
        if not is_daily:
            filtered.append(item)

    return filtered


DAILY_REPORT_PLACEHOLDER_PATTERNS = (
    r"XX亿元",
    r"X\.XX",
    r"X\.X",
    r"Xbp",
    r"XX",
    r"待确认",
    r"人工确认后补充",
    r"示例日期",
)


def daily_report_contains_placeholder(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in DAILY_REPORT_PLACEHOLDER_PATTERNS
    )


def fallback_field_text(value, max_chars: int = 180) -> str:
    text = re.sub(r"\s+", " ", field_to_text(value)).strip()
    if not text or daily_report_contains_placeholder(text):
        return ""
    return text[:max_chars]


def fallback_source_lines(
    items: list,
    label: str,
    title_fields: tuple,
    detail_fields: tuple,
    limit: int = 2,
) -> list:
    lines = []
    seen = set()
    for item in items:
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            continue

        title = next(
            (
                fallback_field_text(fields.get(field_name), max_chars=80)
                for field_name in title_fields
                if fallback_field_text(fields.get(field_name), max_chars=80)
            ),
            "",
        )
        detail = next(
            (
                fallback_field_text(fields.get(field_name))
                for field_name in detail_fields
                if fallback_field_text(fields.get(field_name))
            ),
            "",
        )
        content = "；".join(part for part in (title, detail) if part)
        if not content or content in seen:
            continue
        seen.add(content)
        lines.append(f"- {label}：{content}")
        if len(lines) >= limit:
            break
    return lines


def fallback_cross_asset_lines(market_items: list, limit: int = 6) -> list:
    cross_asset_keywords = (
        "上证指数", "深证成指", "创业板指", "沪深300", "中证500",
        "恒生指数", "纳斯达克", "标普500", "道琼斯",
        "美元兑人民币", "美元指数", "汇率",
        "布伦特原油", "原油", "黄金", "伦敦金", "商品",
    )
    lines = []
    seen = set()

    for item in market_items:
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            continue
        name = fallback_field_text(fields.get("指标名称"), max_chars=60)
        if not name or not any(keyword.casefold() in name.casefold() for keyword in cross_asset_keywords):
            continue

        value = next(
            (
                fallback_field_text(fields.get(field_name), max_chars=60)
                for field_name in ("数值", "最新值", "收盘价")
                if fallback_field_text(fields.get(field_name), max_chars=60)
            ),
            "",
        )
        change = fallback_field_text(fields.get("涨跌幅"), max_chars=60)
        unit = fallback_field_text(fields.get("单位"), max_chars=20)
        if not value and not change:
            continue

        line = f"- {name}"
        if value:
            line += f"：{value}{unit}"
        if change:
            line += f"；涨跌幅 {change}"
            if not change.endswith("%"):
                line += "%"
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def build_fixed_income_daily_fallback(
    market_items: list,
    news_items: list,
    knowledge_items: list,
    report_items: list,
    report_date: str | None = None,
) -> str:
    """模型不可用或输出不可信时，使用真实输入生成唯一一份简洁日报。"""
    report_date = report_date or datetime.now().astimezone().strftime("%Y-%m-%d")
    source_lines = []
    source_lines.extend(
        fallback_source_lines(
            news_items,
            "舆情池",
            ("标题", "主题"),
            ("摘要", "投资含义"),
        )
    )
    source_lines.extend(
        fallback_source_lines(
            knowledge_items,
            "知识库",
            ("素材标题",),
            ("核心结论", "摘要", "投资逻辑"),
        )
    )
    source_lines.extend(
        fallback_source_lines(
            report_items,
            "报告库",
            ("报告标题",),
            ("核心结论", "投资建议", "风险提示"),
        )
    )
    focus_text = (
        "\n".join(source_lines)
        if source_lines
        else "当前舆情池、知识库和报告库均无可用新增内容。"
    )

    cross_asset_lines = fallback_cross_asset_lines(market_items)
    cross_asset_text = (
        "\n".join(cross_asset_lines)
        + "\n以上数据仅作为跨资产参考，不替代固收量化指标。"
        if cross_asset_lines
        else "当前市场数据表未提供可用于跨资产参考的有效数据。"
    )

    qualitative_note = (
        "已保留舆情和研究材料中的真实内容作为定性线索，但缺少固收量化数据，暂不形成量化方向结论。"
        if source_lines
        else "当前缺少固收量化数据及可用研究线索，暂不形成方向判断。"
    )

    return f"""【固收投研日报（简版）】
日期：{report_date}

一、今日固收核心结论
{qualitative_note}

二、今日重点关注
{focus_text}

三、资金面与流动性
当前市场数据表未包含 DR007、R007 等资金利率指标，暂无法进行资金面量化判断。

四、利率债市场
当前未接入国债收益率曲线数据，利率债部分仅依据舆情和研究材料作定性分析。

五、信用债市场
当前未接入信用利差数据，信用债部分暂无量化利差判断。

六、可转债与跨资产表现
{cross_asset_text}

七、宏观与政策
宏观与政策影响仅依据上述真实资料作定性观察，不使用缺失数据进行推断。

八、今日关注与风险提示
后续重点补充资金利率、国债收益率曲线和信用利差数据；当前定性判断可能随政策、流动性及信用事件变化而调整。"""


def daily_report_generation_failed(report_text: str) -> bool:
    if not isinstance(report_text, str) or not report_text.strip():
        return True
    if daily_report_contains_placeholder(report_text):
        return True
    return report_text.strip().startswith(
        (
            "Kimi API Key 还没有配置",
            "Kimi 调用失败：",
            "调用 Kimi 失败：",
        )
    )


async def generate_daily_report(user_text: str) -> str:
    """
    生成固收投研日报正文。
    """
    market_items = []
    news_items = []
    knowledge_items = []
    report_items = []

    if FEISHU_MARKET_TABLE_ID:
        market_items = await read_recent_table_records(FEISHU_MARKET_TABLE_ID, limit=30)

    if FEISHU_NEWS_TABLE_ID:
        news_items = await read_recent_news_records(limit=10)

    if FEISHU_KNOWLEDGE_TABLE_ID:
        knowledge_items = await read_recent_table_records(FEISHU_KNOWLEDGE_TABLE_ID, limit=10)

    if FEISHU_REPORT_TABLE_ID:
        report_items = await read_recent_table_records(FEISHU_REPORT_TABLE_ID, limit=5)

    report_items = filter_historical_daily_reports(report_items)
    market_text = format_market_records_for_daily(market_items)
    news_text = format_news_records_for_daily(news_items)
    knowledge_text = format_records_for_daily("最近知识库素材", knowledge_items)
    report_text = format_records_for_daily("最近报告库记录", report_items)
    report_date = datetime.now().astimezone().strftime("%Y-%m-%d")

    prompt = f"""
请基于以下飞书多维表格资料，生成唯一一份正式的《固收投研日报》。

日报日期：{report_date}

用户指令：
{user_text}

【市场数据：只提供量化事实和价格变化】
{market_text}

【舆情池：提供当日事件与舆情线索】
{news_text}

【知识库：提供历史框架、制度背景和研究逻辑】
{knowledge_text}

【报告库：提供已有研究观点和深度结论】
{report_text}

请严格按照以下九个板块输出，标题不得改名、合并或省略：

一、今日固收核心结论
二、今日重点关注
三、资金面与流动性
四、利率债市场
五、信用债市场
六、可转债与跨资产表现
七、宏观与政策
八、重要舆情与机构观点
九、今日关注与风险提示

写作要求：
1. 跨来源合并分析，不按上述四类资料机械复述；相同事件和相同观点只写一次，只输出一份最终日报。
2. 历史材料只能用于解释，不得覆盖最新市场事实；报告库中的历史日报不得被当作当日新增信息。
3. 发生冲突时，以日期更新者优先、市场事实优先于观点；明确指出分歧和证据，不得把冲突观点同时写成最终结论。
4. 每个重要结论尽量回答“发生了什么、为什么重要、对固收市场的影响、后续观察什么”。
5. 对缺失数据必须明确说明，不得编造或推断。
6. 日报日期只能使用“{report_date}”，不得把历史材料日期或示例日期写成当天日期。
7. 不得输出“XX”“X.X”“X.XX”“XX亿元”“Xbp”“待确认”“人工确认后补充”“示例日期”等占位内容。
8. “今日固收核心结论”先给出偏强、偏弱、震荡或分化等方向判断，再用3—5条说明主要驱动，并明确区分“市场事实”和“研究判断”。
9. “今日重点关注”列出最重要的政策、数据、会议、资金面或海外事件，并说明可能影响的资产或交易方向；没有可靠信息时写“暂无新增重点事件”。
10. “资金面与流动性”只使用资金价格、公开市场操作和流动性指标；缺少资金利率数据时写“暂无新增资金面量化数据”，不得用股票、汇率或商品数据代替。
11. “利率债市场”覆盖国债、政金债、国债期货、收益率曲线和期限利差，并说明为什么重要及对债市的影响；没有相应量化数据时如实说明。
12. “信用债市场”覆盖城投债、产业债、信用利差、等级利差、供给和风险事件；没有信用数据时可依据其他资料作定性判断，但必须标注“证据不足”。
13. 股票、汇率和商品数据只用于“可转债与跨资产表现”中解释其对固收资产的影响，不得占据日报主体。
14. “宏观与政策”中的每条信息都应说明对利率债、信用债或转债的影响方向。
15. “重要舆情与机构观点”汇总有效观点并去重，不得把历史日报或重复观点重新写成当日新增观点。
16. “今日关注与风险提示”列出跟踪变量及可能使当前判断失效的风险，只作研究提示，不写确定性收益承诺。
17. 不得编造输入中不存在的事实、新闻、政策、数据、日期或来源；任何一类资料为空时仍按九个板块生成。
18. 语言正式、简洁，适合基金公司或资管机构固收晨会阅读。
"""

    try:
        generated_report = await call_kimi(prompt, "投研日报")
    except Exception as exc:
        print("生成固收投研日报失败，返回简版日报:", type(exc).__name__)
        generated_report = ""

    if daily_report_generation_failed(generated_report):
        return build_fixed_income_daily_fallback(
            market_items,
            news_items,
            knowledge_items,
            report_items,
            report_date,
        )
    return generated_report


async def handle_daily_report(user_text: str) -> str:
    report_text = await generate_daily_report(user_text)

    title = "固收投研日报-" + datetime.now().strftime("%Y%m%d-%H%M")

    try:
        doc_url = await create_feishu_doc(title, report_text)
        await write_report_record(user_text, report_text, doc_url)

        return (
            report_text
            + f"\n\n【系统提示】已创建飞书文档：\n{doc_url}"
            + "\n\n【系统提示】本次固收投研日报已写入报告库，状态：草稿。"
        )
    except Exception as e:
        print("生成投研日报归档失败:", repr(e))
        return report_text + f"\n\n【系统提示】生成投研日报归档失败：{repr(e)}"


async def query_subject_news_records(keyword: str, limit: int = 20) -> list:
    if (
        not FEISHU_NEWS_TABLE_ID
        or not FEISHU_BITABLE_APP_TOKEN
        or not keyword
        or limit <= 0
    ):
        return []

    try:
        token = await get_tenant_access_token()
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
            f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_NEWS_TABLE_ID}/records/search"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        searchable_fields = ("标题", "主题", "摘要")
        payload = {
            "field_names": [
                "标题",
                "主题",
                "摘要",
                "情绪方向",
                "影响程度",
                "日期",
            ],
            "filter": {
                "conjunction": "or",
                "conditions": [
                    {
                        "field_name": field_name,
                        "operator": "contains",
                        "value": [keyword],
                    }
                    for field_name in searchable_fields
                ],
            },
            "automatic_fields": False,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                headers=headers,
                params={"page_size": min(limit, 100)},
                json=payload,
            )

        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            print(
                "查询深度报告相关舆情失败:",
                data.get("code"),
                data.get("msg"),
            )
            return []

        items = data.get("data", {}).get("items", [])
        if not isinstance(items, list):
            return []
        return [item.get("fields") for item in items if isinstance(item, dict)]
    except Exception as exc:
        print("查询深度报告相关舆情失败，继续生成深度报告:", type(exc).__name__)
        return []


async def read_subject_news_for_deep_report(subject: dict, limit: int = 5) -> str:
    if not FEISHU_NEWS_TABLE_ID or not isinstance(subject, dict) or limit <= 0:
        return ""

    issuer = str(subject.get("issuer") or "").strip()
    stock_code = str(subject.get("stock_code") or "").strip()
    keywords = list(dict.fromkeys(value for value in (issuer, stock_code) if value))
    if not keywords:
        return ""

    candidates = []
    for keyword in keywords:
        try:
            records = await query_subject_news_records(keyword, limit=20)
        except Exception as exc:
            print("查询深度报告相关舆情失败，继续生成深度报告:", type(exc).__name__)
            continue
        if isinstance(records, list):
            candidates.extend(records)

    items = []
    seen = set()
    max_items = min(limit, 5)
    normalized_keywords = [keyword.casefold() for keyword in keywords]

    for fields in candidates:
        if not isinstance(fields, dict):
            continue
        try:
            title = field_to_text(fields.get("标题")).strip()
            topic = field_to_text(fields.get("主题")).strip()
            summary = field_to_text(fields.get("摘要")).strip()[:300]
            subject_text = field_to_text(fields.get("公司/主体")).strip()
            sentiment = field_to_text(fields.get("情绪方向")).strip()
            impact = field_to_text(fields.get("影响程度")).strip()
            published_at = field_to_text(fields.get("日期")).strip()
            searchable = "\n".join((title, topic, summary, subject_text)).casefold()
        except Exception:
            continue

        if not any(keyword in searchable for keyword in normalized_keywords):
            continue
        if not any((title, topic, summary, sentiment, impact, published_at)):
            continue

        identity = (title, topic, summary, sentiment, impact, published_at)
        if identity in seen:
            continue
        seen.add(identity)
        items.append(
            f"【相关舆情 {len(items) + 1}】\n"
            f"标题：{title}\n"
            f"主题：{topic}\n"
            f"摘要：{summary}\n"
            f"情绪方向：{sentiment}\n"
            f"影响程度：{impact}\n"
            f"日期：{published_at}"
        )
        if len(items) >= max_items:
            break

    return "\n\n".join(items)


async def generate_deep_report(
    user_text: str,
    task_type: str,
    knowledge_text: str | None = None,
    public_info_text: str = "",
) -> str:
    from app.providers.registry import official_research_enabled

    official_enabled = official_research_enabled()
    subject = {}
    if official_enabled:
        from app.providers import extract_research_subject

        try:
            extracted_subject = extract_research_subject(user_text)
            if isinstance(extracted_subject, dict):
                subject = extracted_subject
        except Exception as exc:
            print("提取研究主体失败，继续生成深度报告:", type(exc).__name__)

    if knowledge_text is None:
        knowledge_text = await read_knowledge_records(limit=10, user_text=user_text)

    if not official_enabled:
        if knowledge_text or public_info_text:
            enhanced_prompt = f"""
用户原始指令：
{user_text}

【公开信息补充】
{public_info_text or '暂无公开信息补充。'}

以下是飞书多维表格“知识库素材”中沉淀的历史研报、资料、纪要和摘要，请优先参考，但不要机械照搬。

【知识库参考资料】
{knowledge_text or '暂无相关知识库资料。'}

请基于用户指令、公开信息补充和知识库参考资料，生成一篇正式的金融投研深度报告。

要求：
1. 优先使用输入材料中已有事实和逻辑。
2. 不得编造公开信息、知识库和用户指令中没有的数据。
3. 对无法确认的信息，必须写“信息不足”或“需人工确认”。
4. 报告结构应包括：核心结论、行业背景、产业链分析、公司/主体分析、投资逻辑、风险提示、后续跟踪指标。
5. 语言正式，适合作为投研初稿。
"""
            reply_text = await call_kimi(enhanced_prompt, task_type)
            if knowledge_text:
                reply_text += "\n\n【系统提示】本次深度报告已参考飞书多维表格“知识库素材”中的历史资料。"
            return reply_text
        return await call_kimi(user_text, task_type)

    from app.providers import (
        collect_official_evidence,
        format_evidence_for_report,
        format_evidence_index,
    )

    query = str(
        subject.get("issuer")
        or subject.get("stock_code")
        or subject.get("query")
        or ""
    ).strip()

    history_records = []
    if FEISHU_REPORT_TABLE_ID and query:
        try:
            history_records = await query_bitable_records(
                FEISHU_REPORT_TABLE_ID,
                query,
                limit=10,
            )
        except Exception as exc:
            print("查询历史报告失败，继续生成深度报告:", type(exc).__name__)

    history_parts = []
    for index, fields in enumerate(history_records, start=1):
        history_parts.append(
            f"【历史报告 {index}】\n"
            f"报告标题：{field_to_text(fields.get('报告标题'))}\n"
            f"报告类型：{field_to_text(fields.get('报告类型'))}\n"
            f"行业：{field_to_text(fields.get('行业'))}\n"
            f"公司/主体：{field_to_text(fields.get('公司/主体'))}\n"
            f"核心结论：{field_to_text(fields.get('核心结论'))[:500]}"
        )
    if history_parts:
        history_text = "\n\n".join(history_parts)
    elif not query:
        history_text = "未识别到有效研究主体，未查询历史报告。"
    else:
        history_text = "暂无相关历史报告。"

    news_text = await read_subject_news_for_deep_report(subject, limit=5)

    evidence = []
    official_evidence = ""
    try:
        evidence = await collect_official_evidence(user_text)
        if evidence:
            official_evidence = format_evidence_for_report(evidence)
    except Exception as exc:
        print("读取或格式化官方资料失败，继续生成深度报告:", type(exc).__name__)
        evidence = []

    official_section = ""
    additional_instructions = []
    if evidence and official_evidence:
        official_section = f"""
=================

官方资料：

{official_evidence}

=================
"""
        additional_instructions.append(
            "官方资料来源索引由系统另行追加，正文不要生成、改写或补充来源清单。"
        )

    news_section = ""
    if news_text:
        news_section = f"""
【舆情池参考资料】
{news_text}
"""
        additional_instructions.append(
            "舆情仅用于市场关注和事件线索；官方资料优先级高于舆情池，"
            "未经官方资料确认的内容不得写成确定事实，舆情与官方资料冲突时以官方资料为准。"
        )

    additional_requirements = "\n".join(
        f"{index}. {instruction}"
        for index, instruction in enumerate(additional_instructions, start=5)
    )

    prompt = f"""
用户原始指令：
{user_text}

【知识库参考资料】
{knowledge_text or '暂无相关知识库资料。'}

【公开信息补充】
{public_info_text or '暂无公开信息补充。'}

【历史报告参考资料】
{history_text}
{official_section}
{news_section}
请基于用户指令和以上资料，生成一篇正式的金融投研深度报告。

要求：
1. 优先使用输入资料中已有事实和逻辑，不得编造不存在的数据、事实、日期或链接。
2. 对无法确认的信息，必须写“信息不足”或“需人工确认”。
3. 报告结构应包括：核心结论、行业背景、产业链分析、公司/主体分析、投资逻辑、风险提示、后续跟踪指标。
4. 语言正式，适合作为投研初稿。
{additional_requirements}
"""

    reply_text = await call_kimi(prompt, task_type)
    if knowledge_text:
        reply_text += "\n\n【系统提示】本次深度报告已参考飞书多维表格“知识库素材”中的历史资料。"
    if evidence and official_evidence:
        try:
            reply_text += "\n\n" + format_evidence_index(evidence)
        except Exception as exc:
            print("格式化官方资料索引失败，继续返回深度报告:", type(exc).__name__)
    return reply_text


async def feishu_events(request: Request):
    body = await request.json()

    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body.get("challenge")})

    print("Received Feishu event:", body)

    message_id = None
    owns_processing_slot = False

    try:
        research_task = FEISHU_ADAPTER.to_research_task(
            body,
            clean_feishu_mention,
        )
        message = research_task.raw_message
        message_id = research_task.message_id
        message_type = research_task.message_type

        # 文件处理也可能超过飞书重试等待时间，因此所有消息必须先经过同一去重门槛。
        if message_id in PROCESSED_MESSAGE_IDS:
            print("Duplicate completed message ignored:", message_id)
            return {"code": 0, "msg": "duplicate completed ignored"}

        if message_id in PROCESSING_MESSAGE_IDS:
            print("Duplicate processing message ignored:", message_id)
            return {"code": 0, "msg": "duplicate processing ignored"}

        if message_id:
            PROCESSING_MESSAGE_IDS.add(message_id)
            owns_processing_slot = True

        # 先识别文件消息，保留现有下载、解析、Kimi 摘要和知识库写入流程。
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
                PROCESSED_MESSAGE_IDS.add(message_id)
                return {"code": 0, "msg": "file key missing"}

            file_reply_text = ""
            try:
                local_path = await download_feishu_message_file(message_id, file_key, file_name)

                file_text = extract_text_from_file(local_path, max_chars=20000)

                if not file_text.strip():
                    file_reply_text = (
                        "我已下载文件，但没有成功提取正文。\n"
                        f"文件名：{file_name}\n"
                        f"保存路径：{local_path}\n\n"
                        "可能原因：PDF 是扫描件、文件加密、格式暂不支持，或文件内容无法直接提取。"
                    )
                else:
                    summary_text = await summarize_file_with_kimi(file_name, file_text)
                    system_tip = ""
                    try:
                        await write_knowledge_record(
                            f"文件名：{file_name}\n\n正文节选：\n{file_text[:2000]}",
                            summary_text
                        )
                        system_tip = "\n\n【系统提示】本次文件摘要已写入飞书多维表格“知识库素材”，可用于后续报告。"
                    except Exception as e:
                        print("写入知识库素材失败:", repr(e))
                        system_tip = f"\n\n【系统提示】写入知识库素材失败：{repr(e)}"

                    file_reply_text = summary_text + system_tip
            except Exception as e:
                print("处理飞书文件失败:", repr(e))
                file_reply_text = f"我收到了文件，但处理失败：{repr(e)}"

            # 每个文件事件只在这里回复一次，避免成功/异常分支重复发送。
            await reply_feishu_message(message_id, file_reply_text)
            PROCESSED_MESSAGE_IDS.add(message_id)

            if len(PROCESSED_MESSAGE_IDS) > 1000:
                PROCESSED_MESSAGE_IDS.clear()

            return {"code": 0, "msg": "file processed"}

        # 只处理文本消息
        if not message_id or message_type != "text":
            print("Unsupported message type:", message_type, message)
            return {"code": 0, "msg": "ok"}

        user_text = research_task.user_text

        if not user_text:
            reply_text = "我收到了，但没有识别到具体问题。你可以说：帮我梳理今天机器人产业链舆情。"
            await reply_feishu_message(message_id, reply_text)
            PROCESSED_MESSAGE_IDS.add(message_id)
            return {"code": 0, "msg": "ok"}

        normalized_user_text = user_text.strip()
        if normalized_user_text == "官方资料状态":
            reply_text = await handle_official_research_health()
            await reply_feishu_message(message_id, reply_text)
            PROCESSED_MESSAGE_IDS.add(message_id)
            return {"code": 0, "msg": "official research health checked"}

        task_id = str(uuid.uuid4())[:8]
        routed_task = route_task(user_text)
        task_type = TASK_TYPE_LABELS[routed_task]

        await write_task_record(task_id, task_type, user_text, "处理中")

        assistant_result = await FEISHU_ADAPTER.dispatch(
            research_task,
            kimi_handler=call_kimi,
            deepseek_handler=call_deepseek,
            knowledge_provider=read_knowledge_records,
            deep_report_handler=generate_deep_report,
            legacy_daily_handler=handle_daily_report,
            routed_task=routed_task,
        )
        reply_text = assistant_result.content

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
                reply_text = reply_text + f"\n\n【系统提示】已创建飞书文档：\n{doc_url}\n\n【系统提示】本次深度报告已写入飞书多维表格“报告库”，状态：草稿。"
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
        if message_id and owns_processing_slot:
            PROCESSING_MESSAGE_IDS.discard(message_id)

    return {"code": 0, "msg": "ok"}


# Compatibility ASGI entry. Deployment-specific assembly lives in bootstrap.
app = create_app(
    config=APP_CONFIG,
    services=APP_SERVICES,
    feishu_event_handler=feishu_events,
)

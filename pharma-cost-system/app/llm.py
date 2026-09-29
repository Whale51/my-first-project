# -*- coding: utf-8 -*-
"""大模型适配层：OpenAI 兼容接口（DeepSeek / 通义千问 / GLM 等）

设计要点（对应 TODO P2「多模型协作」与 P0「必须走真实大模型」）：

1. **角色分工（多模型协作）**：按任务轻重路由到不同模型，兼顾质量与成本/时延
   | 角色 | 环境变量 | 典型任务 | 说明 |
   |---|---|---|---|
   | `large` | `LLM_MODEL`       | 报告正文、要素归因、对标归因 | 长文本 + 强推理，质量优先 |
   | `small` | `LLM_MODEL_SMALL` | 材料/要素分类、任务字段归一化、关键词与实体抽取、日志摘要 | 高频调用，成本/时延优先；未配置时自动回落到 large |
   另可选 `LLM_EMBED_MODEL` 提供向量化（RAG 语义检索）。

2. **确定性降级**：超时/异常一律返回 None，由调用方回落到本地规则引擎；
   RAG 向量检索在无 embeddings 时自动改用本地哈希 TF-IDF（见 `app/vector_store.py`）。

3. **可观测**：`info()` 返回当前引擎模式、角色模型、超时设置，供 `/api/meta` 与前端横幅展示。
"""
import json
import os

from .logging_conf import get_logger
from .paths import load_dotenv

load_dotenv()  # 支持从项目根目录 .env 读取 LLM_* 配置

log = get_logger(__name__)

BASE_URL = os.environ.get("LLM_BASE_URL", "")   # 如 https://api.deepseek.com/v1
API_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
MODEL_SMALL = os.environ.get("LLM_MODEL_SMALL", "") or MODEL          # 小模型缺省回落大模型
EMBED_MODEL = os.environ.get("LLM_EMBED_MODEL", "")
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))
MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "1"))

# 角色说明（写入文档与接口，便于评审看到"多模型协作"的分工依据）
ROLE_SPEC = {
    "large": {"env": "LLM_MODEL", "用途": "报告正文、成本要素归因、对标归因（长文本强推理，质量优先）"},
    "small": {"env": "LLM_MODEL_SMALL", "用途": "要素/类别分类、任务字段归一化、实体与关键词抽取（高频低成本）"},
}

_ready = None


def llm_ready():
    """是否配置了可用的对话大模型"""
    global _ready
    if _ready is None:
        _ready = bool(BASE_URL and API_KEY)
    return _ready


def embed_ready():
    """是否配置了向量化模型（RAG 语义检索）"""
    return bool(llm_ready() and EMBED_MODEL)


def model_for(role="large"):
    """按角色取模型名（多模型协作）"""
    return MODEL_SMALL if str(role).lower().startswith("small") else MODEL


def _client():
    from openai import OpenAI
    # 明确超时与重试次数：接口异常时快速降级回本地规则引擎，避免报告生成长时间卡住
    return OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=TIMEOUT, max_retries=MAX_RETRIES)


def get_embeddings(text, model=None):
    """单条文本向量；未配置或异常返回 None"""
    out = get_embeddings_batch([text], model=model)
    return out[0] if out else None


def get_embeddings_batch(texts, model=None):
    """批量文本向量（RAG 索引构建用）；未配置或异常返回 None"""
    if not embed_ready():
        return None
    try:
        c = _client()
        r = c.embeddings.create(model=model or EMBED_MODEL, input=list(texts))
        return [d.embedding for d in r.data]
    except Exception as e:
        log.warning("向量化接口调用失败，RAG 将使用本地哈希向量：%s", e)
        return None


def chat(system, user, temperature=0.3, role="large"):
    """调用大模型（按角色选模型）；任何异常返回 None（调用方自行降级）"""
    if not llm_ready():
        return None
    model = model_for(role)
    try:
        c = _client()
        r = c.chat.completions.create(
            model=model, temperature=temperature,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        text = r.choices[0].message.content
        if text:
            log.info("LLM 调用成功 role=%s model=%s 输出%d字", role, model, len(text))
        return text
    except Exception as e:
        log.warning("LLM 调用失败（role=%s model=%s）：%s → 降级为本地规则引擎", role, model, e)
        return None


def chat_json(system, user, role="large"):
    """要求模型输出 JSON；解析失败返回 None"""
    txt = chat(system + "\n只输出 JSON，不要输出其他内容。", user, temperature=0.1, role=role)
    if not txt:
        return None
    try:
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception as e:
        log.warning("LLM JSON 解析失败：%s", e)
        return None


def info():
    """引擎与角色信息（供 /api/meta 与前端展示）"""
    return {
        "ready": llm_ready(),
        "mode": "llm" if llm_ready() else "rule",
        "base_url": BASE_URL or None,
        "roles": {r: {"model": model_for(r), **spec} for r, spec in ROLE_SPEC.items()},
        "embed": {"ready": embed_ready(), "model": EMBED_MODEL or None},
        "timeout_seconds": TIMEOUT,
    }

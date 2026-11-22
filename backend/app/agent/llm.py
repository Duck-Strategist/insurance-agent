"""对话模型：OpenAI 兼容接口（默认 DashScope qwen-plus，可换 DeepSeek，spec D36）。

两种用法：
- json_llm：路由、查询改写，要求输出 JSON、温度 0，结果可复现、好解析；
- answer_llm：生成回答，流式输出给前端。

不用 with_structured_output（函数调用）：各家兼容接口对 tools 的支持不一致，
JSON 模式 + 自己解析更通用，测试里也能用假模型直接返回 JSON 文本。
"""

import json
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import LlmSettings


class LlmNotConfiguredError(Exception):
    pass


def make_llms(
    settings: LlmSettings, api_key: SecretStr | None
) -> tuple[BaseChatModel, BaseChatModel]:
    """返回 (json_llm, answer_llm)。"""
    if api_key is None:
        raise LlmNotConfiguredError("缺少对话模型的 Key：LLM__API_KEY 或 RAG__DASHSCOPE_API_KEY")
    common: dict[str, Any] = {
        "model": settings.model,
        "base_url": settings.base_url,
        "api_key": api_key,
        "timeout": settings.timeout_seconds,
        "max_retries": settings.max_retries,
        # 流式时也让接口在最后一个分片里返回 token 用量（stream_options.include_usage）。
        # 对话图用 messages 流模式，图里的每次模型调用都会走流式，不开的话 trace 里用量全是 0
        "stream_usage": True,
    }
    json_llm = ChatOpenAI(
        **common, temperature=0, model_kwargs={"response_format": {"type": "json_object"}}
    )
    answer_llm = ChatOpenAI(**common, temperature=settings.temperature, streaming=True)
    return json_llm, answer_llm


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def parse_json_object(text: str) -> dict[str, Any]:
    """解析模型输出的 JSON 对象；容忍 ```json 代码块包裹。解析失败返回空字典，由调用方兜底。"""
    cleaned = _FENCE.sub("", text.strip())
    try:
        data = json.loads(cleaned)
    except ValueError:
        # 模型偶尔在 JSON 前后加一句话：取第一个 { 到最后一个 } 再试一次
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            data = json.loads(cleaned[start : end + 1])
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}

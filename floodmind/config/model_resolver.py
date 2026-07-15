"""模型配置单一解析点（SDK 稳定契约）。

把 settings.json 的 providers 目录解析为可直接使用的 ResolvedModel。
所有"用哪个模型"的消费方都应调用 resolve_model()，不再各自解析——
这是消除散落在 ModelConfig / model_presets / provider_registry / model_client
五处解析逻辑的唯一入口。

层级（OpenCode 风格，层层递进）::

    providers.<provider_id>           # 服务商
        name / base_url / api_key     #   连接信息
        models[]                      #   该服务商提供的模型列表
            id / name                 #     单个模型
            context_window            #     模型能力（记忆窗口取此值）
            default_max_tokens        #     生成默认
            default_temperature       #     生成默认
            supports_reasoning/vision #     能力位

激活模型 = catalog 第一个；会话级切换由调用方传 model_key，不写回配置文件。
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from floodmind.config.settings import get_config

# 代码内部默认（模型定义缺失对应字段时兜底，非配置项）
_DEFAULT_TEMPERATURE = 0.3
_DEFAULT_MAX_TOKENS = 8192
_DEFAULT_CONTEXT_WINDOW = 32768


@dataclass(frozen=True)
class ResolvedModel:
    """解析后的完整模型配置——可直接用于构造 ModelClient / DualMemory。"""

    provider: str
    id: str
    name: str
    api_key: str
    base_url: str
    temperature: float        # 来自模型 default_temperature
    max_tokens: int           # 来自模型 default_max_tokens
    context_window: int       # 来自模型 context_window
    supports_reasoning: bool
    supports_vision: bool
    supports_search: bool = False


# ── 内部：目录读取（兼容旧 provider 单数 + dict 形式 models）──────────────

def _normalize_models(raw: Any) -> List[Dict[str, Any]]:
    """models dict（key=model id，旧）或 list（新）→ 统一 list，每个含 id。"""
    out: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for m in raw:
            if isinstance(m, dict) and m.get("id"):
                out.append(m)
    elif isinstance(raw, dict):
        for key, info in raw.items():
            if not isinstance(info, dict):
                continue
            m = dict(info)
            m.setdefault("id", key)
            out.append(m)
    return out


def _providers_section(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """读取 providers 段；缺失时兼容旧 provider（单数）就地归一化。"""
    prov = cfg.get("providers")
    if isinstance(prov, dict) and prov:
        return prov
    legacy = cfg.get("provider")
    if isinstance(legacy, dict):
        return _migrate_legacy_providers(legacy)
    return {}


def _migrate_legacy_providers(legacy: Dict[str, Any]) -> Dict[str, Any]:
    """旧 provider.<id>.options.{apiKey,baseURL} + models.<key> → 新 providers（内存归一化）。"""
    out: Dict[str, Any] = {}
    for pid, pdata in legacy.items():
        if not isinstance(pdata, dict):
            continue
        opts = pdata.get("options")
        opts = opts if isinstance(opts, dict) else {}
        api_key = opts.get("apiKey") or opts.get("api_key") or pdata.get("api_key", "")
        base_url = opts.get("baseURL") or opts.get("base_url") or pdata.get("base_url", "")
        out[pid] = {
            "name": pdata.get("name", pid),
            "base_url": base_url,
            "api_key": api_key,
            "type": pdata.get("type", "openai-compat"),
            "models": _normalize_models(pdata.get("models", {})),
        }
    return out


def list_models() -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """返回 [(provider_id, provider_data, model_dict), ...]，按 settings.json 配置顺序。"""
    cfg = get_config()
    prov = _providers_section(cfg)
    result: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for pid, pdata in prov.items():
        if not isinstance(pdata, dict):
            continue
        for m in _normalize_models(pdata.get("models", [])):
            result.append((pid, pdata, m))
    return result


# ── 公开：唯一解析入口 ──────────────────────────────────────────────────

def resolve_model(
    model_key: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> ResolvedModel:
    """解析模型配置。

    优先级:
      model_key 显式 > FLOODMIND_MODEL 环境变量 > catalog 第一个模型。
    provider_id 可进一步限定服务商。

    api_key/base_url 取自 providers 连接段；
    temperature/max_tokens/context_window 取自模型自身定义（单一真相源）。
    """
    # 环境变量覆盖（保持与历史 FLOODMIND_* 行为一致）
    env_model = os.getenv("FLOODMIND_MODEL", "").strip()
    if not model_key and env_model:
        model_key = env_model
    env_provider = os.getenv("FLOODMIND_PROVIDER", "").strip()
    if not provider_id and env_provider:
        provider_id = env_provider

    candidates = list_models()

    # 1) 精确匹配 model_key（+ 可选 provider 限定）
    if model_key:
        for pid, pdata, m in candidates:
            if m.get("id") == model_key and (not provider_id or pid == provider_id):
                return _build(pid, pdata, m)

    # 2) 仅指定 provider → 该 provider 下第一个模型
    if provider_id:
        for pid, pdata, m in candidates:
            if pid == provider_id:
                return _build(pid, pdata, m)

    # 3) catalog 第一个（全局默认激活模型）
    if candidates:
        pid, pdata, m = candidates[0]
        return _build(pid, pdata, m)

    # 4) 兜底
    raise ValueError(
        "settings.json 未配置任何 providers 模型。"
        "请在 providers.<id>.models[] 中至少添加一个模型。"
    )


def _build(pid: str, pdata: Dict[str, Any], m: Dict[str, Any]) -> ResolvedModel:
    """从目录条目构造 ResolvedModel（连接信息取自 provider，参数取自模型）。"""
    # api_key：env 优先（FLOODMIND_API_KEY / DASHSCOPE_API_KEY），其次 provider 配置
    api_key = os.getenv("FLOODMIND_API_KEY", "").strip()
    if not api_key:
        api_key = (pdata.get("api_key") or "").strip()
    if not api_key and pid == "dashscope":
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()

    base_url = (pdata.get("base_url") or "").strip()
    if not base_url:
        base_url = os.getenv("FLOODMIND_BASE_URL", "").strip()

    return ResolvedModel(
        provider=pid,
        id=m.get("id", ""),
        name=m.get("name", m.get("id", "")),
        api_key=api_key,
        base_url=base_url,
        temperature=float(
            m.get("default_temperature", m.get("temperature", _DEFAULT_TEMPERATURE))
        ),
        max_tokens=int(
            m.get("default_max_tokens", m.get("maxTokens", m.get("max_tokens", _DEFAULT_MAX_TOKENS)))
        ),
        context_window=int(
            m.get("context_window", m.get("maxTokens", _DEFAULT_CONTEXT_WINDOW))
        ),
        supports_reasoning=bool(
            m.get("supports_reasoning", m.get("supportsReasoning", False))
        ),
        supports_vision=bool(
            m.get("supports_vision", m.get("supportsVision", False))
        ),
        supports_search=bool(
            m.get("supports_search", m.get("supportsSearch", False))
        ),
    )

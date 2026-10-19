"""G-06：子进程/沙箱环境中的凭证隔离。

宿主进程的环境变量里通常带有模型 API Key、云厂商 Token、密码等敏感值。
凡是交给子进程（Bash、后台任务、MCP stdio server、沙箱后端）的环境，都必须
先经过本模块过滤，否则 prompt injection 只需一条 `env` 或
`echo $OPENAI_API_KEY` 就能把凭证带出沙箱。

策略：按变量名做结构化匹配，而不是按值猜测。以下名字会被移除：

    *_API_KEY / API_KEY
    *_KEY / KEY
    *_TOKEN / TOKEN
    *_SECRET / SECRET
    *_PASSWORD / *_PASSWD / *_PASS
    *_CREDENTIAL(S)
    *_AUTH / AUTH
    *_PRIVATE*
    *_ACCESS_KEY*
    *_COOKIE*

边界按 ``_`` 或字符串首尾匹配，因此 ``TOKENIZERS_PARALLELISM``、``KEYBOARD``
之类的普通变量不会被误伤。

需要凭证的子进程（如 MCP server）必须由配置显式声明 env：那是"显式授权注入"，
不是隐式继承；调用方在过滤后的安全环境上合并配置值即可。
"""

from typing import Dict, Iterable, Mapping, Optional

from floodmind.config.secret_store import is_sensitive_key as is_credential_env_key


def strip_credential_env(env: Mapping[str, str]) -> Dict[str, str]:
    """返回剔除凭证类变量后的新环境字典（不修改入参）。"""
    return {
        str(key): str(value)
        for key, value in env.items()
        if not is_credential_env_key(key)
    }


def apply_env_policy(
    env: Mapping[str, str],
    *,
    allowlist: Optional[Iterable[str]] = None,
    required: Iterable[str] = (),
    inject: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """统一的环境策略：先剔除凭证，再按需应用允许列表，最后注入显式配置。

    Args:
        env: 基础环境（通常是宿主 os.environ）。
        allowlist: 允许列表；为空/None 时保留过滤后的全部变量。
        required: 无论允许列表如何都必须保留的系统变量名。
        inject: 显式授权注入的变量（例如 MCP server 配置的 env），最后合并。
    """
    safe = strip_credential_env(env)
    if allowlist:
        keep = {str(key) for key in allowlist}
        keep.update(str(key) for key in required)
        safe = {key: value for key, value in safe.items() if key in keep}
    if inject:
        safe.update({str(key): str(value) for key, value in inject.items()})
    return safe

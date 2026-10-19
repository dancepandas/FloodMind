"""G-06：配置文件中的敏感值静态加密。

问题：provider API Key、网关 token、搜索 Key、MCP server 的鉴权 header 等
会以明文落在 ``~/.floodmind/*.json``。提示注入只需读取并外传配置文件即可
拿到凭证。

本模块提供透明的静态加密：

- Windows：DPAPI（``CryptProtectData``），由登录用户/机器托管密钥，无需
  额外密钥文件；复制到其他机器或用户后无法解密。
- 其他平台：Fernet，密钥文件为 FloodMind home 下的 ``.secret_key``（0600）。

密文格式为 ``enc:v1:<base64>``。读取时解密失败属于 fail-closed：该项视为
空值并记录告警，绝不把密文当作凭证继续使用。明文值原样通过，因此已有配置
无需迁移即可继续工作，下一次保存时自动转为密文。
"""

import base64
import logging
import os
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

ENC_PREFIX = "enc:v1:"

# 敏感字段名：配置键与子进程环境变量共用同一套命名判断。
_SENSITIVE_NAME = re.compile(
    r"(^|_)("
    r"API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|"
    r"CREDENTIALS?|AUTH|PRIVATE|ACCESS_KEY|COOKIE"
    r")(_|$)",
    re.IGNORECASE,
)

# headers 的鉴权类名字（Authorization / X-Api-Key / *-token 等）。
_SECRET_HEADER_HINT = re.compile(
    r"(authorization|api[-_]?key|token|secret|password|cookie)",
    re.IGNORECASE,
)


def is_sensitive_key(name: Any) -> bool:
    """判断字段/环境变量名是否属于凭证类。"""
    return bool(_SENSITIVE_NAME.search(str(name)))


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


# ── 平台后端 ────────────────────────────────────────────────

def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DataBlob()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    ):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DataBlob()
    description = wintypes.LPWSTR()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), ctypes.byref(description), None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        if description:
            kernel32.LocalFree(description)
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _key_path():
    # 延迟导入避免 settings 与本模块的循环依赖。
    from floodmind.config.settings import get_floodmind_home

    return get_floodmind_home() / ".secret_key"


def _fernet():
    from cryptography.fernet import Fernet

    key_path = _key_path()
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
        finally:
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
        logger.info("已生成本地配置加密密钥: %s", key_path)
    return Fernet(key)


# ── 单值 API ────────────────────────────────────────────────

def protect(value: Any) -> Any:
    """加密一个敏感字符串；空值/非字符串/已加密值原样返回。"""
    if not isinstance(value, str) or not value or is_encrypted(value):
        return value
    raw = value.encode("utf-8")
    if os.name == "nt":
        blob = _dpapi_protect(raw)
    else:
        blob = _fernet().encrypt(raw)
    return ENC_PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")


def unprotect(value: Any) -> Any:
    """解密一个 ``enc:v1:`` 值；明文原样返回。

    解密失败（换了机器/用户、密文损坏）时返回空串并告警：fail-closed，
    避免把不可解密的字符串当成凭证使用。
    """
    if not is_encrypted(value):
        return value
    try:
        blob = base64.urlsafe_b64decode(value[len(ENC_PREFIX):].encode("ascii"))
        if os.name == "nt":
            raw = _dpapi_unprotect(blob)
        else:
            raw = _fernet().decrypt(blob)
        return raw.decode("utf-8")
    except Exception as exc:
        logger.warning("配置密文解密失败，按空值处理（fail-closed）: %s", exc)
        return ""


# ── 配置树 API ──────────────────────────────────────────────

def _protect_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
    return {
        str(name): protect(str(value))
        if isinstance(value, str) and _SECRET_HEADER_HINT.search(str(name))
        else value
        for name, value in headers.items()
    }


def _unprotect_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
    return {
        str(name): unprotect(value) if isinstance(value, str) else value
        for name, value in headers.items()
    }


def protect_tree(value: Any, _key: str = "") -> Any:
    """返回配置树的加密副本（不修改入参）。

    按字段名加密：``api_key``/``token``/``password`` 等；``headers`` 中的鉴权
    header；``env``/``environment`` 字典中名字敏感的条目。
    """
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if isinstance(item, str) and is_sensitive_key(name):
                out[key] = protect(item)
            elif name.lower() == "headers" and isinstance(item, dict):
                out[key] = _protect_headers(item)
            elif name.lower() in ("env", "environment") and isinstance(item, dict):
                out[key] = protect_tree(item)
            elif isinstance(item, (dict, list)):
                out[key] = protect_tree(item, name)
            else:
                out[key] = item
        return out
    if isinstance(value, list):
        return [protect_tree(item, _key) for item in value]
    return value


def unprotect_tree(value: Any) -> Any:
    """返回配置树的解密副本（不修改入参）。"""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if isinstance(item, str) and is_encrypted(item):
                out[key] = unprotect(item)
            elif name.lower() == "headers" and isinstance(item, dict):
                out[key] = _unprotect_headers(item)
            elif isinstance(item, (dict, list)):
                out[key] = unprotect_tree(item)
            else:
                out[key] = item
        return out
    if isinstance(value, list):
        return [unprotect_tree(item) for item in value]
    return value

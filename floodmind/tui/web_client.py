"""FloodMind TUI — HTTP 客户端，连接 web server API

替代直接调用 agent.stream()，通过 HTTP API 与 web server 交互，从而共享会话数据。
"""

import json
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

import httpx


class FloodMindClient:
    """FloodMind Web Server 的 HTTP 客户端"""

    def __init__(self, base_url: str = "http://localhost:13014"):
        self.base_url = base_url.rstrip("/")
        self.session_id = f"tui-{uuid.uuid4().hex[:8]}"
        self.client = httpx.Client(timeout=300.0)
        self._init_ok = False
        self.model_name = ""

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def health_check(self, timeout: float = 2.0) -> bool:
        """检测 web server 是否正在运行"""
        try:
            resp = self.client.get(f"{self.base_url}/api/health", timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def wait_for_ready(self, timeout: float = 60.0, interval: float = 1.0) -> bool:
        """轮询 /api/health 直到 web server 就绪或超时"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.health_check(timeout=2.0):
                return True
            time.sleep(interval)
        return False

    def init_session(
        self,
        enable_search: bool = False,
        enable_rag: bool = True,
        enable_reasoning: bool = True,
        model_key: str = "",
    ) -> bool:
        """POST /api/init，建立会话。成功返回 True。"""
        try:
            payload: Dict[str, Any] = {
                "session_id": self.session_id,
                "enable_search": enable_search,
                "enable_rag": enable_rag,
                "enable_reasoning": enable_reasoning,
            }
            if model_key:
                payload["model_key"] = model_key
            resp = self.client.post(f"{self.base_url}/api/init", json=payload, timeout=30.0)
            if resp.status_code == 200:
                data = resp.json()
                self.model_name = data.get("model_name", model_key or "(unknown)")
                self._init_ok = True
                return True
            return False
        except Exception:
            return False

    def stream_chat(self, message: str, enable_reasoning: bool = True) -> Iterator[Dict[str, Any]]:
        """POST /api/chat，yield NDJSON 流式事件"""
        if not self._init_ok:
            if not self.init_session(enable_reasoning=enable_reasoning):
                yield {"type": "error", "content": "无法连接 web server 或初始化失败"}
                return

        payload = {
            "session_id": self.session_id,
            "message": message,
            "enable_reasoning": enable_reasoning,
        }
        try:
            with self.client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=600.0,
            ) as resp:
                if resp.status_code != 200:
                    body = resp.read()
                    yield {"type": "error", "content": f"HTTP {resp.status_code}: {body[:200]}"}
                    return
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        yield event
                        if event.get("type") == "stream_end":
                            return
                    except json.JSONDecodeError:
                        continue
        except httpx.TimeoutException:
            yield {"type": "error", "content": "请求超时（>10 分钟）"}
        except Exception as e:
            yield {"type": "error", "content": f"网络错误: {e}"}

    def list_sessions(self) -> List[Dict[str, Any]]:
        """GET /api/sessions"""
        try:
            resp = self.client.get(f"{self.base_url}/api/sessions", timeout=10.0)
            if resp.status_code == 200:
                return resp.json().get("sessions", [])
        except Exception:
            pass
        return []

    def list_models(self) -> List[Dict[str, Any]]:
        """GET /api/models"""
        try:
            resp = self.client.get(f"{self.base_url}/api/models", timeout=10.0)
            if resp.status_code == 200:
                return resp.json().get("models", [])
        except Exception:
            pass
        return []

    def clear_memory(self) -> bool:
        """POST /api/clear"""
        try:
            resp = self.client.post(
                f"{self.base_url}/api/clear",
                json={"session_id": self.session_id},
                timeout=10.0,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def pause_session(self) -> bool:
        """POST /api/session/pause"""
        try:
            resp = self.client.post(
                f"{self.base_url}/api/session/pause",
                json={"session_id": self.session_id},
                timeout=10.0,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def resume_session(self) -> bool:
        """POST /api/session/resume"""
        try:
            resp = self.client.post(
                f"{self.base_url}/api/session/resume",
                json={"session_id": self.session_id},
                timeout=10.0,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def update_config(self, **kwargs) -> bool:
        """POST /api/session/config，支持 model_key / enable_search / enable_rag / enable_reasoning"""
        try:
            payload = {"session_id": self.session_id, **kwargs}
            resp = self.client.post(
                f"{self.base_url}/api/session/config",
                json=payload,
                timeout=10.0,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def get_session_status(self) -> Optional[Dict[str, Any]]:
        """GET /api/session/status"""
        try:
            resp = self.client.get(
                f"{self.base_url}/api/session/status",
                params={"session_id": self.session_id},
                timeout=10.0,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

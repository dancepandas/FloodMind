"""工具输出语义：显式状态优先，普通文本按固定失败前缀归一。

ToolOutput 保留正文接口，并携带错误状态与副作用 metadata。生产者应显式
聚合分段结果；执行层和重试保护共享此判定，不能扫描正文推断显式结果的状态。
将 ToolOutput 转为普通 str 会丢失这些语义，应在消费状态和 metadata 后再转换。

尚返回普通文本的工具按开头固定失败前缀判定，不扫描正文中的错误词。
"""

from typing import Tuple

_FAILURE_PREFIXES: Tuple[str, ...] = (
    "错误：",
    "错误:",
    "[执行失败",
    "执行失败",
    "命令执行失败",
    "命令执行超时",
    "读取失败",
    "读取文件失败",
    "写入失败",
    "搜索失败",
    "搜索文件失败",
    "搜索内容失败",
    "搜索请求失败",
    "搜索超时",
    "未找到技能",
    "中未找到脚本",
    "未配置",
    "缺少必要的依赖库",
    "添加知识失败",
    "文档添加失败",
    "添加长期记忆失败",
    "搜索记忆失败",
    "记忆系统未初始化",
    "[重试保护]",
)


class ToolOutput(str):
    """带显式执行语义的文本；正文不参与结构化结果的状态判定。"""

    def __new__(cls, content: str, *, is_error: bool = False, metadata=None):
        instance = super().__new__(cls, content)
        instance.is_error = is_error
        instance.metadata = dict(metadata or {})
        return instance


def tool_output_is_error(output: str) -> bool:
    """优先使用显式状态，普通文本按工具失败前缀判定。"""
    if isinstance(output, ToolOutput):
        return output.is_error
    head = (output or "").lstrip()
    if not head:
        return False
    return head.startswith(_FAILURE_PREFIXES)

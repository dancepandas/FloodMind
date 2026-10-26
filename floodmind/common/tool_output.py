"""工具输出语义：识别内置工具约定的失败输出。

内置工具（Read/Bash/Grep/GetSkill 等）把可预期失败编码为带固定前缀的
文本返回，而不是抛异常。执行层用本模块把这一约定归一为
``ToolResult.status == "error"``，让宿主、指标、journal 与重试逻辑看到真实
结果，而不是把"文件不存在"记成一次成功调用。

判定只看输出开头：工具失败信息总是以固定前缀开头，而 Read 等工具返回的
文件正文前面有头部，不会被正文里的"错误："字样误判。
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


def tool_output_is_error(output: str) -> bool:
    """判断工具返回文本是否表示一次失败（按开头前缀判定）。"""
    head = (output or "").lstrip()
    if not head:
        return False
    return head.startswith(_FAILURE_PREFIXES)

# 新增与修改工具契约

适用范围：原生 SDK 中的本地工具定义、适配、注册与执行。本文是开发约束，不是新运行时设计；实现与本文冲突时，先明确差异并补回归证据，不增加兼容补丁掩盖冲突。

## 最小可运行示例

此块由显式登记的文档检查器加载，并由 `tests/test_development_examples.py` 通过真实 `ToolExecutionService` 执行。普通 Python 围栏不自动执行。

```python contract-example=tool-basic
from pydantic import BaseModel, ConfigDict, Field

from floodmind.tools.agent_tool import build_agent_tool
from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy


class MeanFlowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: list[float] = Field(min_length=1, description="非空流量序列")


def mean_flow(values: list[float]) -> str:
    return f"{sum(values) / len(values):.2f}"


def make_tool():
    return build_agent_tool(
        func=mean_flow,
        name="mean_flow",
        description="计算非空流量序列的平均值；不读写外部状态。",
        args_schema=MeanFlowInput,
        is_readonly=True,
        is_destructive=False,
        is_concurrency_safe=True,
        permission_policy=ToolPermissionPolicy(policy_type="readonly"),
    ).to_tool_spec()
```

本例只验证执行服务边界，不代表完整 Agent 循环、权限问询、journal 持久化或恢复已被覆盖。测试中的最小注册表与占位 journal authority 仅用于无事务的纯只读调用，不能复制为宿主事务实现。

## 定义与接入

- 公开定义入口是 `floodmind.tools.agent_tool.build_agent_tool`；`AgentTool.to_tool_spec()` 是转换权威。不得自行复制转换规则。
- 原生实例注册表接收 `ToolSpec` 或 `AgentTool`，按名字存储；同名注册会覆盖。调用方负责唯一命名，不能假定框架拒绝重复。
- 通过当前宿主/Agent 的工具注入路径接入实例注册表，不依赖模块全局注册表传播状态，不直接依赖私有 `_InstanceToolRegistry` 作为公共 SDK。
- `parameters` 优先于从 `args_schema` 生成的 schema。二者同时提供时，开发者必须保证一致；推荐单一 Pydantic 输入定义。
- 描述必须说明用途、输入单位与约束、输出含义、可能副作用。不要把动态用户指令拼入工具定义。

## 框架保证与执行顺序

当前实现依据：`floodmind/agent/runtime/services/tool_execution_service.py`、`floodmind/agent/runtime/contracts/tools.py`、`floodmind/tools/agent_tool.py`、`floodmind/agent/native/tool_runtime.py`。

1. 解析工具并清理参数键。
2. 对 `parameters` 执行 JSON Schema 验证；不合法参数不会进入业务函数。
3. 处理权限与问询。`is_readonly` 是元数据，不是访问路径或网络的沙箱；声明不能代替权限策略。
4. 运行自定义 `validate_input`，再按 `args_schema` 验证和转换 handler 参数。业务约束应使用受支持的 Pydantic schema 或明确的验证钩子，不能假设任意对象都具备严格验证语义。
5. 调用业务函数，捕获异常，归一化输出并限制大小。

权限可能返回允许、拒绝或等待问询；自定义业务验证发生在权限处理之后，不应承担必须在权限前执行的安全检查。权限策略类型与 planning/subagent 门控以权限服务为准，不允许工具自行绕过。

## 输出、失败与重试

- 结果协议是 `ToolResult`：`status` 为 `completed`、`error` 或 `awaiting_permission`；保留调用 ID 和工具名。
- handler 返回值转为文本，`None` 为空文本；输出会被截断/外置，已知错误文本前缀会归一为 error。不要把它描述成自动结构化 JSON 结果协议。
- 参数错误应得到 `INPUT_VALIDATION_FAILED`，业务异常应得到 `TOOL_EXECUTION_ERROR`；调用者不能只看非空字符串判定成功。
- 超时通过后台线程等待实现，不能安全强杀线程。超时后的执行状态可能是 `indeterminate_running`，业务副作用可能稍后完成；不得自动原样重试，更不得声称已取消或已回滚。
- 有副作用操作必须自行设计幂等性、实际结果核验和事务接入；一次超时不是一次无副作用失败。

## 开发者责任与禁止模式

| 必须 | 禁止及替代 |
| --- | --- |
| 如实声明只读、破坏性与并发安全性 | 用默认只读掩盖写文件/网络行为；改用真实策略及测试 |
| 将路径、命令、网络范围交给权威权限边界 | handler 自建旁路执行或放宽全局权限 |
| 明确可重试性、共享状态和清理责任 | 将异常吞成成功文字；保留错误语义 |
| 对共享状态采取适当同步 | 将 `is_concurrency_safe=True` 当成自动锁 |
| 复用转换和注册入口 | 在多个工具里复制 schema/权限/输出归一逻辑 |
| 保持纯业务逻辑与运行时接入分层 | 工具直接修改执行器、历史、checkpoint 内部状态 |

## 修改与验收

修改名称、参数、输出或权限时，同步所有注册者、调用者、模型描述、示例、规范及测试。删除旧入口时直接迁移消费者，不增加隐式 fallback。

至少提供：成功路径、缺失/类型/边界/额外字段输入、拒绝与问询（适用时）、业务异常、副作用核验，以及并发/超时/重试（适用时）。权限拒绝与输入失败应证明 handler 未运行。

现有证据：`tests/test_tool_execution_service.py` 覆盖执行/超时不确定性；`tests/test_tool_output_status.py` 覆盖错误输出归一；本文示例测试覆盖纯只读执行、输入拒绝和异常。

运行：

```bash
python -m pytest tests/test_development_examples.py tests/test_tool_execution_service.py tests/test_tool_output_status.py -q
```

完整变更还须遵循 [变更验证矩阵](../testing/change-matrix.md)，不能用这条局部命令替代全量回归。

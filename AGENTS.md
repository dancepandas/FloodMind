# FloodAgent 项目指南

## 项目概述
FloodAgent（洪水预报智能体）是一个基于 LangChain 的多 Agent 洪水预报系统。
采用 Dispatcher + Execution Specialist 双层架构：
- **调度 Agent**：负责意图分析、任务规划、分发、结果汇总
- **执行单元**：负责单步任务的落地执行（运行脚本、生成文件等）

## 技术栈
- 语言：Python 3.10+
- LLM：Qwen（通义千问）via DashScope OpenAI 兼容 API
- 框架：LangChain（OpenAI Functions Agent）
- 前端：React 19 + TypeScript + Vite + TailwindCSS
- 向量库：ChromaDB（RAG）
- 部署：Docker（PyTorch + CUDA 基础镜像）

## 目录结构
```
agent/          FloodAgent 核心（调度器、执行单元、Agent Loop）
tools/          工具定义（base_tools.py 为主）
skills/         技能包（每个子目录一个 skill，含 SKILL.md + scripts/）
config/         配置管理（settings.py 从环境变量加载）
models/         LLM 服务封装（qwen_llm_service.py）
memory/         记忆系统（DualMemory：短期对话 + 长期记忆）
rag/            RAG 检索（Embedding + VectorStore + Retriever）
web_server.py   Flask Web 服务（REST API + NDJSON 流式推送）
web/            Web 前端（React + 旧版静态页）
data/           运行时数据（sessions、vector_store、tool_error_memory）
```

## Agent 行为约束
- 调度 Agent 不得亲自执行脚本、写文件、构造 JSON 等具体任务
- 执行单元不得重新规划、不得扩展上下游任务
- 必须先调用 `create_plan` 创建执行计划，再分发任务
- 每次只分发给执行单元一个核心动作
- 严禁把超长 JSON 直接塞进工具参数
- 涉及 10 条以上数据时必须整理为 Excel 文件输出
- 最终输出不得包含系统完整路径或会话内部信息

## Skill 开发规范
每个 skill 是 `skills/` 下的一个子目录，必须包含：
- `SKILL.md`：YAML frontmatter（name, description）+ 使用说明
- `scripts/`：可执行 Python 脚本

新增 skill 时：
1. 在 `skills/` 下创建子目录
2. 编写 `SKILL.md`
3. 将脚本放入 `scripts/`
4. skill 会由 `skills/__init__.py` 自动发现并注册

### 脚本输出路径约定
- `run_script` / `exec_python_file` 的工作目录（cwd）已自动设为当前会话的输出目录
- 脚本的输出文件参数**只写文件名**即可，例如 `--output_file result.json`，不要写 `data/sessions/.../result.json` 或任何目录前缀
- 如果写成了 `data/sessions/result.json`，文件会存到 `输出目录/data/sessions/result.json`（路径嵌套错误），后续产物检查将找不到文件
- 脚本如需获取输出目录的绝对路径，读取环境变量 `SESSION_OUTPUT_DIR`

## 安全边界
- `write_text_file` 只允许写入 `data/sessions/` 和项目根目录下的文件
- `exec_bash` 禁止执行 `rm -rf`、`del /s`、`format`、`rmdir /s` 等破坏性命令
- `exec_bash` 禁止访问 `/etc/`、`C:\Windows\`、`C:\Program Files\` 等系统目录
- 工具输出超过 8000 字符时自动截断，完整结果保存至文件
- 工具连续 3 次相同调用失败后触发重试保护

## 代码风格
- Python 代码遵循 PEP 8
- 不添加非必要注释
- 使用 logging 模块记录日志，不用 print

## word默认风格
- 正文中文字体使用`宋体小四号`
- 正文英文字体使用`Times New Roman小四号`
- 正文文本格式`两端对齐，单倍行距`
- 文本题目使用`黑体小二号，段后1行`
- 一级标题使用`黑体四号，段后1行`
- 二级标题使用`黑体小四号，段后1行`
- 三级标题使用`宋体小四号，段后1行`
- 表格表名使用`中文宋体五号，英文Times New Roman五号`
- 表格内字体使用`中文宋体五号，英文Times New Roman五号`
- 图名使用`中文宋体五号，英文Times New Roman五号`
- 表格和图片必须有对应的表名和图名

## 绘图默认风格
- 必须设置图例
- **必须严格按以下模板编写绘图脚本开头**（import 顺序不可变，`mpl.use('Agg')` 必须在 `import pyplot` 之前）：
```python
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import fontManager

for f in fontManager.ttflist:
    if f.name in ('SimSun', '宋体', 'Noto Sans CJK SC', 'WenQuanYi Zen Hei'):
        mpl.rcParams['font.sans-serif'] = [f.name, 'Times New Roman'] + mpl.rcParams['font.sans-serif']
        break
mpl.rcParams['font.family'] = 'sans-serif'
mpl.rcParams['axes.unicode_minus'] = False
```

## 常见陷阱
- DashScope 的 reasoning_content 可能返回增量或累计文本，回调中需要兼容两种模式
- Qwen 模型的 tool_call 参数有时会以 JSON 字符串形式传入，需要 `_parse_json_if_needed` 兼容
- Chronos 模型首次加载较慢（~30s），需要预热
- Excel sheet 名称最长 31 字符，stationCode 过长时会被截断
- 脚本输出路径只写文件名（`result.json`），不要写 `data/sessions/.../result.json`，否则路径嵌套导致文件找不到
- Excel sheet 名称最长 31 字符，stationCode 过长时会被截断
- matplotlib 在无头环境必须设置 `MPLBACKEND=Agg`

# scripts/knowledge_graph — 架构知识图谱生成

静态分析 Python/TypeScript 源码，生成文件、符号、导入、调用与继承关系图，并输出 Mermaid、循环依赖报告与离线 HTML 可视化。

## 主要模块

| 文件 | 职责 |
| --- | --- |
| `__main__.py` | CLI：生成整图与报告 |
| `model.py` | Node / Edge / Graph 数据模型 |
| `py_analyzer.py` | Python AST：文件、符号、import、call、inherit |
| `ts_analyzer.py` | TypeScript/JavaScript 正则导入分析 |
| `layers.py` | 路径到架构分层的唯一映射源 |
| `subsystems.py` | 人工维护的子系统节点与依赖边 |
| `report.py` | 指标、Mermaid、循环依赖、未解析导入 |
| `viz.py` | 单文件离线交互式 HTML |
| `query.py` | 查询已生成的 `graph.json` |

## 运行

```bash
python -m scripts.knowledge_graph [--root .] [--out docs/knowledge-graph/graph] [--no-ts]
python -m scripts.knowledge_graph.query <path-or-symbol> [--in|--out|--symbols]
```

## 约定

- 新增或移动目录时先更新 `layers.py` 与 `subsystems.py`，再重新生成并提交 `graph/` 产物。
- 分析器不执行被分析代码。
- 跨模块属性调用、反射/动态注册、线程池投递不会出现在调用图中，不能当作完整调用链。

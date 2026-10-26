# floodmind/profile — 身份与行为指导

系统提示词中“身份层”和“行为层”的来源。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `soul.py` | 默认身份、默认 SOUL.md、`load_soul_md()`、`seed_default_soul()` |
| `guidance.py` | 行为指导常量：记忆、工作方法、工具执行、工作流、定时任务、偏好、产物判断、输出格式 |

## 约定

- 工具说明由 `ToolRegistry` 自动注入，这里只写“怎么做事”，不要复制工具描述。
- `SOUL.md` 读取上限 20,000 字符，超出截断。
- `seed_default_soul()` 仅在文件不存在时创建，不覆盖用户内容。
- 部分 guidance 依据工具是否存在条件注入；新增或重命名工具时需同步 `native_flood_agent.py` 的判断。

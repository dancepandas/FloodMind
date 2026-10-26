# floodmind/config — 配置与模型解析

全局配置、模型目录、Provider 注册、搜索配置与敏感值加密的统一入口。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `settings.py` | 配置加载与迁移；环境变量 > `~/.floodmind/settings.json` > 内置模板；导出全局 `settings` |
| `model_resolver.py` | 模型配置单一解析点：`resolve_model()`、`resolve_context_window()`、`list_models()` |
| `model_presets.py` | 模型预设注册表与模型列表 |
| `provider_registry.py` | Provider 定义、模型名解析、传输校验 |
| `secret_store.py` | API Key/token 静态加密（Windows DPAPI / 其他平台 Fernet，密文前缀 `enc:v1:`） |
| `search_config.py` | WebSearch 配置读写 |
| `settings_template.json` | 默认配置模板 |

## 约定

- 所有“用哪个模型”的消费方必须调用 `resolve_model()`，禁止新增散落解析逻辑。
- 显式 `model_key` 未命中时抛 `ValueError`，不做静默回退到其他模型。
- 自定义模型可用 `ModelClient(context_window=...)` 显式声明窗口，绕过 catalog。
- 解密失败必须 fail-closed（视为空值并告警），不得继续使用密文。
- 导入配置不得改变进程级 DNS/TLS/网络策略；代理策略放在 HTTP client（见 `native/transport.py`）。
- `model_presets.get_models_list()` 的输出形状是前端模型选择器契约，修改需同步前端。

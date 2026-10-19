# 文件级循环依赖

## 循环 1（2 个文件）

- `floodmind/skills/base.py`
- `floodmind/skills/registry.py`

内部边：

- `floodmind/skills/base.py` → `floodmind/skills/registry.py`
- `floodmind/skills/registry.py` → `floodmind/skills/base.py`

## 循环 2（6 个文件）

- `floodmind/agent/native/model_client.py`
- `floodmind/config/model_presets.py`
- `floodmind/config/model_resolver.py`
- `floodmind/config/provider_registry.py`
- `floodmind/config/secret_store.py`
- `floodmind/config/settings.py`

内部边：

- `floodmind/agent/native/model_client.py` → `floodmind/config/provider_registry.py`
- `floodmind/agent/native/model_client.py` → `floodmind/config/model_resolver.py`
- `floodmind/agent/native/model_client.py` → `floodmind/config/model_presets.py`
- `floodmind/config/model_presets.py` → `floodmind/config/model_resolver.py`
- `floodmind/config/model_presets.py` → `floodmind/config/settings.py`
- `floodmind/config/model_resolver.py` → `floodmind/config/settings.py`
- `floodmind/config/model_resolver.py` → `floodmind/config/provider_registry.py`
- `floodmind/config/provider_registry.py` → `floodmind/config/model_resolver.py`
- `floodmind/config/provider_registry.py` → `floodmind/config/settings.py`
- `floodmind/config/provider_registry.py` → `floodmind/agent/native/model_client.py`
- `floodmind/config/secret_store.py` → `floodmind/config/settings.py`
- `floodmind/config/settings.py` → `floodmind/config/secret_store.py`
- `floodmind/config/settings.py` → `floodmind/config/model_resolver.py`
- `floodmind/config/settings.py` → `floodmind/config/model_presets.py`

## 循环 3（2 个文件）

- `floodmind/tools/base_tools.py`
- `floodmind/tools/file_tools.py`

内部边：

- `floodmind/tools/base_tools.py` → `floodmind/tools/file_tools.py`
- `floodmind/tools/file_tools.py` → `floodmind/tools/base_tools.py`

## 循环 4（2 个文件）

- `floodmind/cli.py`
- `floodmind/cli_interactive.py`

内部边：

- `floodmind/cli.py` → `floodmind/cli_interactive.py`
- `floodmind/cli_interactive.py` → `floodmind/cli.py`

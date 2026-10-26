# floodmind/models — ModelClient 兼容导出

兼容导出层。真正的模型客户端实现在 `floodmind.agent.native.model_client`。

## 内容

- `ModelClient`：从 `floodmind.agent.native.model_client` 重导出，支持 `from floodmind.models import ModelClient`。

## 约定

- 本目录不新增模型逻辑；修改模型行为应改 `agent/native/model_client.py` 与 `agent/native/providers/`。
- 包根 `floodmind.ModelClient` 与此处指向同一实现。

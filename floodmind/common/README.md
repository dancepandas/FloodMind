# floodmind/common — 低层共享基础设施

不依赖 floodmind 其他子系统的通用能力，供运行时、工具层与网关共用。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `filelock.py` | 跨进程排他文件锁（Windows `msvcrt` / POSIX `fcntl`）、`fsync_file()` |
| `process_tree.py` | 进程组隔离与进程树终止（Windows taskkill / POSIX 独立 session） |
| `tool_output.py` | `tool_output_is_error()`：按固定前缀识别内置工具的文本失败 |

## 约定

- 本包不得引入 floodmind 内部依赖，保持可独立测试。
- `FileLock` 只保证同线程内可重入；跨线程互斥需另加锁。
- 锁超时抛 `FileLockTimeoutError`，由调用方决定 fail-closed 或降级，不要静默无限等待。
- POSIX 子进程启动时必须先 `isolate_process_group()`，否则终止逻辑只能杀直接子进程，避免 `killpg` 误杀调用者。
- 修改 `tool_output.py` 的失败前缀时，必须与内置工具的实际返回文案同步。

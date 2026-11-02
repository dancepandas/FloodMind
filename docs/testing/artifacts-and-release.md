# 产物与发布验收

源码目录可导入不代表 wheel 可用。发布验证必须检查干净受控输入以及真正安装后的资源与入口。

## 产物检查

```bash
python -m pip install build
python scripts/check_distribution.py
```

检查器从 `git ls-files -z` 获取受控文件，排除构建缓存、仓库元数据、虚拟环境和未跟踪文件，在临时源副本中构建 sdist，从本次 sdist 构建 wheel，再安装到临时 venv。它不删除当前 dist/build/egg-info，不修改全局 Python 环境。

探针在源码外目录以 `-I` 隔离模式检查公共 Agent/ModelClient/ToolSpec、模块安装路径、版本元数据、settings 模板和内置 Skill；随后从同一源码外目录运行临时 venv 真正生成的 `Scripts/floodmind.exe --help`（Windows）或 `bin/floodmind --help`（POSIX），不以直接调用 Python 函数代替控制台入口。依赖安装可能需要网络；构建工具仅属开发/CI 依赖。

## 证据边界

- Mock 子进程的单元测试只证明检查器控制流，不能证明产物可安装；必须实际运行脚本。
- 受控工作树副本仍可能包含未提交的已跟踪修改；它不是自动的 clean commit 证明，未跟踪文件不进入包输入。
- 正式发布必须从目标提交的 `git archive` 干净提取物运行全量检查。归档不含 Git 元数据，产物检查必须显式传入同一提交的 NUL 分隔清单；不得递归复制所有文件作为无 Git 时的 fallback。

以下命令使用 Bash，在原 checkout 中生成归档与清单，路径均为源码树外的新建验收目录；`<commit>` 必须是同一个明确提交：

```bash
git archive --format=tar --output=/absolute/acceptance/source.tar <commit>
git ls-tree -r --name-only -z <commit> > /absolute/acceptance/source-files.list
```

将归档解包至该验收目录下独立的新空目录，在提取根目录执行：

```bash
python -m pytest -q
python scripts/check_distribution.py --source-manifest /absolute/acceptance/source-files.list
```

清单仅授权同一提交的路径，检查器仍拒绝越界路径、符号链接、缺失文件和空清单，并排除缓存；不得用工作树 `git ls-files` 的结果冒充目标提交清单。归档生成、解包和清单路径应由维护者记录，连同 commit、Python、平台、命令和结果保存。该流程不提交或发布任何内容。

当前工作树存在未提交修改时，必须明确说明验证对象，不能把 WIP 通过写成已提交发布版通过。本地 Windows 验证不等于 Linux 验证；远端矩阵未运行时如实列出。

## 发布前

确认版本/元数据一致、包资源与公开入口可用、全量回归和适用 eval 通过、CI 证据完整、发布范围不含秘密/业务材料。提交、推送、打 tag、发布和远端保护配置分别需要明确授权；本检查器不执行这些操作。

"""多代理收益基线（G-22）。

对比 single-agent 与 delegated（允许 SubAgent/ParallelTask）两种策略在固定任务集上的
成功率、token 成本与墙钟时间，为“是否值得拆分”提供可重复证据。实现不引入多代理框架，
只复用现有 ``floodmind.Agent`` 运行入口。
"""

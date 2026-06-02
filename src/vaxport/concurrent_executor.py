"""并发 Agent 执行器 — ThreadPoolExecutor 并行调度

支持:
- 无依赖任务并行执行
- 多 Agent 结果合并
- 超时控制
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from vaxport.agent import ProgressCallbacks


class ConcurrentExecutor:
    """并发执行多个 Agent 任务。"""

    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers

    def run_parallel(self, tasks: list[dict],
                     timeout: int = 300) -> list[dict]:
        """并行执行多个 Agent 任务。

        Args:
            tasks: [{"agent": Agent实例, "query": str, "callbacks": ProgressCallbacks}, ...]
            timeout: 总超时(秒)

        Returns:
            按原始顺序排列的结果列表 [result_dict, ...]
            如果某个任务失败，对应位置为 {"error": str}
        """
        if not tasks:
            return []

        if len(tasks) == 1:
            # 单任务直接执行，无需线程开销
            t = tasks[0]
            try:
                return [t["agent"].run(t["query"],
                        callbacks=t.get("callbacks", ProgressCallbacks()))]
            except Exception as e:
                return [{"error": str(e)}]

        results: dict[int, dict] = {}
        errors: dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(tasks))) as executor:
            futures = {}
            for i, task in enumerate(tasks):
                agent = task["agent"]
                query = task["query"]
                cb = task.get("callbacks", ProgressCallbacks())
                fut = executor.submit(agent.run, query, callbacks=cb)
                futures[fut] = i

            for fut in as_completed(futures, timeout=timeout):
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    errors[idx] = str(e)

        # 按原始顺序组装结果
        ordered = []
        for i in range(len(tasks)):
            if i in results:
                ordered.append(results[i])
            elif i in errors:
                ordered.append({"error": errors[i]})
            else:
                ordered.append({"error": "任务超时或未执行"})

        return ordered

    def run_sequential(self, tasks: list[dict]) -> list[dict]:
        """串行执行（有依赖的任务链）。

        Args:
            tasks: 同上格式，按依赖顺序排列
        """
        results = []
        for task in tasks:
            try:
                cb = task.get("callbacks", ProgressCallbacks())
                result = task["agent"].run(task["query"], callbacks=cb)
                results.append(result)
            except Exception as e:
                results.append({"error": str(e)})
                break  # 依赖链中断
        return results
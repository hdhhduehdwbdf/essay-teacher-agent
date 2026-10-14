# -*- coding: utf-8 -*-
"""任务队列消费者：从 DB 认领待执行对话并产出事件。

独立进程运行（生产推荐，可与 web 实例分开扩缩容）：
    python worker.py            # 默认 4 个消费线程，环境变量 WORKERS 可调
    EMBED_WORKERS=0 python web_server.py   # web 进程不内嵌消费者

同一会话 FIFO、不同会话并行由 store.job_claim 的认领规则保证；
多个 worker 进程可以同时跑，不会重复执行同一任务。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api  # noqa: E402
import store  # noqa: E402

IDLE_SLEEP = float(os.environ.get("WORKER_IDLE_SLEEP", "0.15"))


def consume_once():
    """认领并执行一个任务；没任务返回 False。"""
    job = store.job_claim()
    if not job:
        return False
    api.run_job(job)
    return True


def loop(stop=None):
    while not (stop and stop.is_set()):
        try:
            if not consume_once():
                time.sleep(IDLE_SLEEP)
        except Exception as e:                      # 单个任务失败不拖垮 worker
            print(f"[worker] 异常：{type(e).__name__}: {e}")
            time.sleep(1)


def start_threads(n=4, stop=None):
    threads = []
    for _ in range(max(1, int(n))):
        t = threading.Thread(target=loop, args=(stop,), daemon=True)
        t.start()
        threads.append(t)
    return threads


def main():
    store.init_schema()
    n = int(os.environ.get("WORKERS", "4"))
    print(f"[worker] 启动 {n} 个消费线程，后端={store.backend_name()}")
    start_threads(n)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("[worker] 退出")


if __name__ == "__main__":
    main()

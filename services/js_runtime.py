"""在行情线程开始前完成 MiniRacer 首个上下文的串行预热。"""
from threading import Lock


_init_lock = Lock()
_initialized = False


def initialize_js_runtime() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        from py_mini_racer import MiniRacer

        # 仅初始化 V8 库仍不足以建立原生地址池；必须实际创建一次上下文。
        # macOS 上多个行情线程同时首次创建上下文会触发不可捕获的原生断言。
        with MiniRacer() as runtime:
            runtime.eval("0")
        _initialized = True

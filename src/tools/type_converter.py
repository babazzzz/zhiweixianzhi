"""
==========================================
 类型转换工具 — type_converter.py
==========================================
【为什么需要这个模块？】
LangGraph 使用 msgpack 序列化状态（state），
但 numpy.float64、numpy.int64 等类型 msgpack 不认识。

这个模块把所有 numpy 类型转为 Python 原生类型（float, int, list, dict），
确保状态能被正确序列化和反序列化。
"""

import numpy as np


def to_native(obj):
    """
    递归地将对象中的 numpy 类型转换为 Python 原生类型。

    支持的类型：
    - numpy.float64 → float
    - numpy.int64 → int
    - numpy.bool_ → bool
    - numpy.ndarray → list
    - numpy 嵌套在 dict/list 中也处理
    """
    if isinstance(obj, dict):
        return {k: to_native(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [to_native(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(to_native(item) for item in obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return to_native(obj.tolist())
    else:
        return obj

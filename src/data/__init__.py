"""数据层：确定性仿真 + 工况调制 + 实时推进。

数据从哪来、是否每次重跑、如何更贴现实——三个问题的代码答案都在这个包里，
另见 ``data_provenance`` 表（每批数据的血缘记录）。
"""

from .realtime import RealtimeEngine
from .workload import WorkloadContext, modulate_metrics, workload_for

__all__ = ["RealtimeEngine", "WorkloadContext", "workload_for", "modulate_metrics"]

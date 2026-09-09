"""维护报告 Skill 的输入/输出模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MaintenanceReportInput(BaseModel):
    """生成维护报告所需的最小上下文。

    diagnostic_id / notification_id / order_id 均可选：给了就写入报告头并
    关联溯源；没给则自动取该设备最近一次诊断。
    """

    machine_id: str = Field(..., min_length=1, description="设备编号，如 B2012")
    diagnostic_id: str | None = Field(None, description="关联诊断 ID（缺省取最近一次）")
    notification_id: str | None = Field(None, description="触发本报告的审批通知 ID")
    order_id: str | None = Field(None, description="关联维修工单 ID")
    approved_by: str | None = Field(None, description="批准人（管理员）")
    include_history: bool = Field(True, description="是否附历史维修记录与相似案例")


class MaintenanceReportOutput(BaseModel):
    """报告生成结果：结构化摘要 + 完整 Markdown 正文。"""

    report_id: str = Field(..., description="落库后的报告 ID（RPT-xxx）")
    machine_id: str
    title: str
    summary: str = Field(..., description="一段话摘要，供通知/列表页预览")
    markdown: str = Field(..., description="完整 Markdown 报告正文")
    sections: list[str] = Field(default_factory=list, description="正文章节名，便于前端锚点")
    data_sources: list[str] = Field(default_factory=list, description="报告引用的数据来源，保证可追溯")

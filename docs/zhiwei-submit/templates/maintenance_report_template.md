## 设备预测性维护分析报告

> 数据基础：约7小时短时序监测（30采样点）| 数据集特征：故障注入场景（故障率72.3%）
> 100台设备中15台支持三表融合全链路分析，85台仅提供日志级健康评估
> 本报告采用适合短序列的统计分析方法（滑动窗口+指数退化+威布尔RUL），非深度时序模型

**设备**：{equipment_id} | **风险等级**：{risk_level} | **健康评分**：{health_score}/100
**数据范围**：{raw_overview.data_counts.total_records}条日志记录 | 全链路分析：{full_chain_support}

### 一、运行状态与健康基线

| 指标 | 均值 | P5基线 | P95基线 | 波动(Std) | 趋势 |
|------|------|--------|---------|-----------|------|
| 温度(℃) | {raw_overview.temperature.mean} | {raw_overview.temperature.p5} | {raw_overview.temperature.p95} | {raw_overview.temperature.std} | {raw_overview.trends.temperature} |
| 电流(A) | {raw_overview.current.mean} | {raw_overview.current.p5} | {raw_overview.current.p95} | {raw_overview.current.std} | {raw_overview.trends.current} |
| 电压(V) | {raw_overview.voltage.mean} | {raw_overview.voltage.p5} | {raw_overview.voltage.p95} | {raw_overview.voltage.std} | {raw_overview.trends.voltage} |
| 转速(RPM) | {raw_overview.speed.mean} | {raw_overview.speed.p5} | {raw_overview.speed.p95} | {raw_overview.speed.std} | {raw_overview.trends.speed} |

功率：{raw_overview.power.mean}W | 数据量：{raw_overview.data_counts.total_records}条
安全窗口：温度 {raw_overview.safe_ranges.temperature} | 电流 {raw_overview.safe_ranges.current}
设备役龄成熟度：{raw_overview.degradation.equipment_maturity}分（{raw_overview.degradation.age_tier}）

### 二、异常检测（五重融合引擎 + 制造商规格交叉验证）

综合异常评分：**{anomaly_summary.anomaly_score}/100**
检测到 {anomaly_summary.total_anomaly_events} 个异常事件，最长连续 {anomaly_summary.root_cause_analysis.continuous_abnormal_count} 次
CUSUM累积偏差：温度 {anomaly_summary.root_cause_analysis.cusum_scores.temperature} 分 | 电流 {anomaly_summary.root_cause_analysis.cusum_scores.current} 分
制造商规格交叉验证：{anomaly_summary.root_cause_analysis.mfg_spec_cross_validation.mfg_spec_confirmed}个异常同时超出制造商规格阈值（高置信度）
异常特征：{list:anomaly_summary.root_cause_analysis.abnormal_features}
根因判定：{list:anomaly_summary.root_cause_analysis.main_root_causes}

### 三、跨设备统计学习诊断（多视角基线偏差 + 故障模式相似度匹配 + 历史案例检索）

> 方法说明：因每设备仅30条短时序数据，不适用ML模型训练。本引擎采用跨设备聚合统计学习——
> 方法A「多视角基线偏差分析」：5组特征子集投票计算当前设备偏离跨设备正常基线的程度
> 方法B「故障模式相似度匹配」：将当前特征向量与已知故障类型统计中心进行马氏距离匹配
> 方法C「跨设备历史案例检索」：基于10类故障指纹库，匹配最相似的历史设备案例

| 指标 | 数值 | 说明 |
|------|------|------|
| 融合评分 | {dual_model_engine.fused_score}/100 | 偏差分析+相似度匹配+指纹库一致性 自适应加权 |
| 基线偏差评分 | {dual_model_engine.rf_anomaly_score}/100 | 当前设备偏离正常统计基线的程度 |
| 两方法一致性 | {dual_model_engine.model_agreement} | consistent=一致可信；divergent=建议复核 |
| 融合权重 | 偏差:{dual_model_engine.fusion_weight_deviation} 相似度:{dual_model_engine.fusion_weight_similarity} | 基于方差的自适应融合 |

预测故障：**{predicted_fault.fault_name}**（置信度 {predicted_fault.confidence}，即{confidence_pct}%）
Top3故障概率：{top3_faults_text}
最具区分力的特征：{list:dual_model_engine.top_discriminative_features}

> 历史案例参考：{history_cases_text}

### 四、退化趋势与RUL预测

退化阶段：{raw_overview.degradation.degradation_stage}
参数波动：温度 Std={raw_overview.degradation.temperature_volatility} | 电流 Std={raw_overview.degradation.current_volatility}
温度分布：偏度 {raw_overview.temperature.skewness}（>0右偏偶发高温）| 峰度 {raw_overview.temperature.kurtosis}（>0厚尾极端值多）| CV {raw_overview.temperature.cv}
自相关ACF：Lag1={raw_overview.acf.lag_1} | Lag3={raw_overview.acf.lag_3}（>0.7说明强周期性）

预测剩余寿命：**{rul_analysis.predicted_rul_hours} 小时**（{rul_analysis.rul_level}）
维护紧迫度：{rul_analysis.maintenance_urgency}
> 模型回溯验证：{degradation_analysis.backtest_validation.overall_accuracy}，平均MAPE {degradation_analysis.backtest_validation.avg_mape_pct}%（基于中期→短期窗口回测）
预测故障时间：{rul_analysis.pre_failure_time} | 故障阶段：{rul_analysis.failure_stage}
RUL置信区间：P10={rul_analysis.rul_confidence_interval.p10_optimistic}h | P90={rul_analysis.rul_confidence_interval.p90_pessimistic}h

> 不确定性评估（v5.0）：认知不确定={rul_analysis.uncertainty_decomposition.epistemic.level} | 偶然不确定={rul_analysis.uncertainty_decomposition.aleatoric.level} | 综合不确定度={rul_analysis.uncertainty_decomposition.total_uncertainty}
> 说明：{rul_analysis.uncertainty_decomposition.recommendation}

未来120分钟预测温度：{degradation_analysis.future_temperature}℃
风险升级概率：{future_probability.risk_probability}% | 路径：{future_probability.risk_evolution_path}

### 五、诊断证据链

{evidence_lines}

诊断置信度：{diagnosis_engine.confidence}
结论：{diagnosis_engine.diagnosis_reason}
> 质量风险：{quality_risk_text}
建议：{list:diagnosis_engine.maintenance_reason}

### 六、经营成本与维修工单

| 项目 | 金额 |
|------|------|
| 计划维修（备件+人工） | ¥{cost_analysis.plan_cost} |
| 紧急维修 | ¥{cost_analysis.emerg_cost} |
| 日停机损失 | ¥{cost_analysis.shut_loss} |
| 预测性维护ROI | ¥{cost_analysis.roi_savings} |

日产量 {cost_analysis.daily_out} 件 | 单位成本 ¥{cost_analysis.unit_cost} | 日产值 ¥{cost_analysis.daily_revenue}
经营效率评估：{cost_analysis.econ_assessment}（经济影响分 {cost_analysis.econ_impact_score}/100）
故障停机冲击：预计损失产量 {cost_analysis.efficiency_analysis.fault_impact.production_loss_units} 件，价值 ¥{cost_analysis.efficiency_analysis.fault_impact.production_loss_value}

> 三大约束落地评估（成本优化·效率优先·质量优先）：
> 成本: {cost_analysis.triple_constraint.cost_optimization.assessment} (评分{cost_analysis.triple_constraint.cost_optimization.score})
> 效率: {cost_analysis.triple_constraint.efficiency_priority.assessment} (评分{cost_analysis.triple_constraint.efficiency_priority.score})
> 质量: {cost_analysis.triple_constraint.quality_priority.assessment} (评分{cost_analysis.triple_constraint.quality_priority.score})
> 综合约束评分: {cost_analysis.triple_constraint.composite_score}/100
> 行动建议: {cost_analysis.triple_constraint.recommended_actions}

工单 {work_order.work_order_id} | {work_order.priority_label} | {work_order.failure_type} | ¥{work_order.estimated_cost}
{work_order_steps}

### 七、可视化图表

{chart_section}

# 期刊知识服务 Agent：MVP 测评集 v1

## 目标

这套测评用于判断首版产品是否可以进入小范围作者和编辑试用。

- 真实用户场景负责判断任务是否完成。
- A1–A8 用于定位失败发生在哪一种原子能力。
- 自动检查负责结构、操作、证据覆盖和明显答案约束。
- 人工评分负责判断最终结果是否正确、有用、边界清楚。

## 文件

- `mvp_eval_specs_v1.json`：18 条测评规格，编辑和作者各 9 条。
- `run_mvp_eval.py`：运行生产链路并保存阶段结果。
- `mvp_human_scores_v1.csv`：双盲人工评分表。
- `mvp_eval_results_v1.json`：运行后生成，不应作为固定测试数据提交。

## 数据与能力口径

当前代码中的结构化事实源实际为 SQLite；`MySQLRepo` 已是 `SQLiteRepo` 的兼容别名。向量检索使用 Chroma，关系查询使用 Neo4j；部分关系操作当前仍可降级到 SQLite。

A1–A8 映射：

| 编号 | 能力 |
|---|---|
| A1 | 条件检索 Retrieve |
| A2 | 语义检索 Semantic Search |
| A3 | 特征提取 Extract |
| A4 | 聚合统计 Aggregate |
| A5 | 趋势计算 Trend |
| A6 | 比较 Compare |
| A7 | 图关系分析 Graph Analysis |
| A8 | 排序与判断 Rank / Assess |

## 使用方法

只校验测评集格式和覆盖矩阵，不调用模型：

```bash
python3 eval/run_mvp_eval.py --dry-run
```

先运行少量案例验证环境：

```bash
python3 eval/run_mvp_eval.py --ids MVP-E01,MVP-A01
```

运行全部案例：

```bash
python3 eval/run_mvp_eval.py
```

每条结果会记录：

1. 意图识别结果；
2. Query Plan；
3. Plan Validator 结果；
4. 执行轨迹和各 OperationResult；
5. Coverage Report；
6. Evidence Bundle；
7. Quality Report；
8. 各阶段耗时和错误；
9. 最终答案；
10. 自动检查结果与空白人工评分字段。

## 人工评分

每个维度采用 0–2 分：

- 0：错误、缺失或误导；
- 1：部分满足，需要人工实质修正；
- 2：完整、准确，无需实质修正。

评分维度：

1. `task_completion`：是否真正完成用户任务；
2. `grounding`：事实和证据是否一致；
3. `boundary_control`：澄清、假设、降级或拒绝是否正确；
4. `clarity`：表达是否清楚；
5. `actionability`：结果能否支持下一步行动。

单案例通过条件：

- 不触发硬失败；
- 总分至少 8/10；
- `grounding` 和 `boundary_control` 均不低于 1。

高风险案例由两名评审独立评分。两名评审总分相差超过 2 分，或是否触发硬失败的判断不一致时，交由第三名评审复核。

## MVP 暂定门槛

- 核心场景通过率至少 80%；
- 边界状态判断准确率至少 80%；
- 编辑和作者任一角色通过率不得低于 70%；
- 编造事实、证据错配、越权泄露等严重错误必须为 0；
- 人工 `grounding` 平均分至少 1.5/2；
- 简单检索 P95 小于 3 秒，复杂趋势分析 P95 小于 15 秒。

这些门槛用于内部 MVP 试用，不是最终生产 SLA。完成第一轮运行后，应根据真实基线调整，但不能放宽严重事实错误为 0 的门禁。

## 失败归因

人工复核时将失败归入一个首要阶段：

- `understand`：角色、对象、实体、时间或状态理解错误；
- `plan`：A1–A8选择、依赖或参数错误；
- `execute`：SQLite、Chroma、Neo4j执行或数据错误；
- `evidence`：证据缺失、错配或无法回溯；
- `answer`：证据正确但最终表述错误；
- `product_boundary`：本应澄清、降级或拒绝却强行作答。

修复后将线上或测评失败案例加入固定回归集，不直接修改原题来迎合当前模型输出。

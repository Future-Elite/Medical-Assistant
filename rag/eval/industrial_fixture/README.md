# 工业级图试点数据（v6.7.2）

`vlbw_feeding_2015_graph.json` 是单份指南的图试点，不是指南库。

- 指南：VLBW 喂养 2015（Nutrients，DOI `10.3390/nu7010423`，PMID `25580815`）。
- 节点：4 条建议（达到全量喂养时间、开奶时间、奶类选择、喂养耐受/残余），每条带 `evidence_level`、`timeframe`、`exceptions`、`source_locator`。
- 条件是 `synthetic_fixture`，来自本地解析全文的建议/条件/例外，**没有临床或指南专家复核**。
- `exceptions.kind`：`hard`（硬例外，触发后需人工复核是否暂缓）、`related`（相关情况，只升 `needs_review`）、`negative`（指南明确“不是禁忌”的情况）。
- 文本证据提取只读患者级临床字段，排除 `presentingSymptoms` 中的母亲病史；否定词/不确定词不当作确诊。
- 结果不是临床建议；`exception_triggered` 不是停喂医嘱。

运行：

```bash
PYTHONPATH=. python3 tools/run_v672_industrial_pilot.py \
  --dataset-dir <private dataset dir>
```

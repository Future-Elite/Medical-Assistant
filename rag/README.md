# RAG v5

`v5` 是 v4 的独立升级版：保留只读证据检索、PubMed E-utilities、检索时间/撤回过滤、跨来源去重、来源排序、引用隔离、可选 LLM 摘要和回答审计，并新增检索后的关键证据句定位。

## 新增能力

每条 citation 仍保留 v4 的完整 `quote`、来源和 `open_evidence` 动作，同时新增 `key_evidence_sentences`：

- `sentence_id`：绑定文献的稳定句子标识
- `sentence_index`：原文中的从零开始句序
- `text`：原文句子，不改写
- `start` / `end`：原文字符偏移，可直接回溯验证
- `score`：查询词覆盖度，仅表示透明的词面匹配程度
- `locator`：如 `sentence 1 (chars 0-12)`
- `matched_terms`：命中的中英文词元

提取器位于 `evidence.py`，只使用标准库，支持中英文混合文本。它在文档过滤和排序完成后运行，不改变 v4 的文档排名、时间状态、引用 ID 或审计输入。没有正向词面匹配时返回空列表，不用低相关句伪装成支持证据。

关键句是定位辅助，不是临床正确性判断、指南符合性判断或 MiniCheck 的替代。使用者仍需核对完整摘要/原文和引用定位。

## 启动

```bash
python code/src/v5/server.py
# open http://127.0.0.1:8788/
```

如果 v4 服务已经占用端口，请在 `config.py` 中将 `server_port` 改为其他本地端口。v5 配置默认不含 API key；需要使用 LLM 时，仅在本地配置经过授权的标准 HTTP OpenAI-compatible API。不要把真实患者资料或凭据写入请求、日志、测试和仓库。

当前 GUI 只注册实时 PubMed 连接器；查询结果的引文详情面板会显示完整摘录与关键证据句、句序、字符偏移、匹配度和命中词。LLM 摘要仍仅使用本轮引用证据，回答审计仍按完整检索文档执行。

## 测试

在 `code/src` 下运行：

```bash
python -m unittest discover -s v5/tests -p 'test_*.py'
python -m compileall -q v5
```

测试使用合成/录制数据，不调用实时 PubMed、LLM 或 MiniCheck 下载。`v4` 目录保持独立，v5 测试不导入 v4。

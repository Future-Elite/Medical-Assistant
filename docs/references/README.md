# 文献参考说明

本目录存放「医学真实感改造」设计所依据的公开文献与下载的全文。

- 设计文档：[../superpowers/specs/2026-09-21-medical-realism-design.md](../superpowers/specs/2026-09-21-medical-realism-design.md)
- 全文目录：`papers/`

---

## 一、先分清两件事：「我读了」与「设计中参考了」

用户要求说明「读了哪些文献、参考了哪些文献」。这两者不同，必须分开写清楚。

### 1.1 我实际打开并核对过的（11 篇）

这 11 篇的 PDF 我已下载，并用 `pdfinfo` 核对页数与元数据、用 `pdftotext` 核对首页标题与作者。
**核对的是「这份 PDF 确实是这篇论文」**，不等于我通读了全文。

### 1.2 设计**实际参考**的（6 篇）

只有以下 6 篇的内容真正进入了设计决策：

| 文献 | 参考了它的什么 | 用在设计哪一节 |
|---|---|---|
| Synthea（Walonoski 2018） | 疾病模块状态机、事件日历、慢病终身携带的整体架构 | §5 患者级疾病轨迹 |
| Miletic & Sariyar（BMC 2026） | 「**clinical realism 关注不足**」「inferential validity 关注不足」——本设计的立论缺口 | §1.1、根因⑤ |
| Esteban 2017 | **TSTR** 评估范式（合成集训练 / 真实集测试） | §12 评估方法 |
| Snoke 2018 | **pMSE** 用于合成数据的效用度量及其零假设分布 | §12 量化对比的口径 |
| Woo 2009 | pMSE 的**最早出处** | §12 同上 |
| Chen 2024（arXiv:2411.04281） | 方法五分类（规则驱动 / GAN / VAE / Transformer / 扩散）——论证为何选「规则驱动」 | §3.2 |

其余 5 篇（RareGraph-Synth、RareSyn、Provenance Gap、Semantic Reasoning、Raab 2017）
属**同类工作与技术背景**，用于「相关工作」章节的定位与对比，未改变设计决策。

### 1.3 ⚠️ 一处必须说明的缺口：Synthea 原文我没有读到

Synthea 那篇 JAMIA 论文**不是开放获取**（Europe PMC 返回 `isOpenAccess: "N"`，
且全文接口返回 HTTP 403）。**我没有、也不会绕过付费墙。**

因此：**本设计对 Synthea 架构的描述（状态机、`Delay` 状态、事件日历）来自既有知识，
不是来自阅读原文。** 论文中若要引用 Synthea 的具体机制，**必须先通过学校图书馆取得原文确认**。
请勿在未读原文的情况下写出「Synthea 采用 X 机制」这类断言。

---

## 二、已下载全文（11 篇，均为开放获取）

全部位于 `papers/`，按第一作者与年份命名。核实方式：
`pdfinfo` 核对元数据 + `pdftotext -f 1 -l 1` 核对首页标题与作者。

| # | 文件 | 文献 | 出处 / 链接 | 与设计的关系 |
|---|---|---|---|---|
| 1 | `Esteban2017_RCGAN_TSTR.pdf` | Esteban C, Hyland SL, Rätsch G. Real-valued (Medical) Time Series Generation with Recurrent Conditional GANs | [arXiv:1706.02633](https://arxiv.org/abs/1706.02633) | **TSTR 原始出处**（13 页，首页自述「合成集训练、真实集评估」） |
| 2 | `Kartoun2016_VirtualPatientRepositories.pdf` | Kartoun U. A Methodology to Generate Virtual Patient Repositories | [arXiv:1608.00570](https://arxiv.org/abs/1608.00570) | 虚拟患者库的早期方法 |
| 3 | `Chen2024_ScopingReview_Benchmarking.pdf` | Chen X, Wu Z, Shi X, Cho H, Mukherjee B. Generating Synthetic EHR Data: a Methodological Scoping Review with Benchmarking on Phenotype Data and Open-Source Software | [arXiv:2411.04281](https://arxiv.org/abs/2411.04281) | **方法五分类**，论证选规则驱动路线 |
| 4 | `Uppalapati2025_RareGraphSynth_KnowledgeGuided.pdf` | Uppalapati K, Abdulkareem S, Yimenicioglu B, et al. RareGraph-Synth: Knowledge-Guided Diffusion Models for Generating Privacy-Preserving Synthetic Patient Trajectories in Ultra-Rare Diseases | [arXiv:2510.06267](https://arxiv.org/abs/2510.06267) | **同类工作**：KG 嵌入扩散噪声调度 |
| 5 | `Ahmed2026_ProvenanceGap_TemporalKG.pdf` | Ahmed MS, Dusanic M, Kirschner MN, et al. The Provenance Gap in Clinical AI: Evidence-Traceable Temporal Knowledge Graphs for Rare Disease Reasoning | [arXiv:2604.17114](https://arxiv.org/abs/2604.17114) | 同类工作：时序知识图谱 |
| 6 | `Sherafatmandjoo2026_SemanticReasoning_MedicalKG.pdf` | Sherafatmandjoo H, Akbari M, Rahmati Z. Semantic Reasoning in Medicine: The Role of Knowledge Graphs Across Five Key Domains | [arXiv:2606.15155](https://arxiv.org/abs/2606.15155) | 背景：医学知识图谱综述 |
| 7 | `Miletic2025_LongitudinalTimeSeriesReview_BMC.pdf` | Miletic M, Sariyar M. Synthetic data generation methods for longitudinal and time series health data: a systematic review. *BMC Med Inform Decis Mak*. 2026;26:30 | [doi:10.1186/s12911-025-03326-8](https://doi.org/10.1186/s12911-025-03326-8) | **立论核心引证**（见下方摘录） |
| 8 | `Snoke2018_GeneralSpecificUtility_pMSE_preprint.pdf` | Snoke J, Raab GM, Nowok B, Dibben C, Slavkovic A. General and specific utility measures for synthetic data（*JRSS-A* 2018;181(3):663-688 的**预印本**） | [arXiv:1604.06651](https://arxiv.org/abs/1604.06651) | **pMSE 用于合成数据**及零假设分布 |
| 9 | `Raab2017_GuidelinesUsefulSyntheticData_preprint.pdf` | Raab GM, Nowok B, Dibben C. Guidelines for Producing Useful Synthetic Data | [arXiv:1712.04078](https://arxiv.org/abs/1712.04078) | 合成数据效用评估的实操建议 |
| 10 | `Woo2009_pMSE_GlobalUtilityMeasures.pdf` | Woo MJ, Reiter JP, Oganian A, Karr AF. Global Measures of Data Utility for Microdata Masked for Disclosure Limitation. *J Privacy and Confidentiality*. 2009;1(1) | [doi:10.29012/jpc.v1i1.568](https://doi.org/10.29012/jpc.v1i1.568) | **pMSE 最早出处** |
| 11 | `RareSyn2025_EMNLP.pdf` | Wang H, Zhao Y, Zheng Y, Wu X. RareSyn: Health Record Synthesis for Rare Disease Diagnosis（EMNLP 2025） | [ACL Anthology](https://aclanthology.org/2025.emnlp-main.620/) | 同类工作：KG + LLM 合成 EHR |

### 立论核心摘录（可直接引用，出自第 7 篇原文）

> Utility evaluations vary widely, with strong emphasis on descriptive statistics and predictive
> performance, but **limited attention to inferential validity and clinical realism**.
> Privacy assessments are sparse and inconsistently reported: only 30% of studies included any
> metric, and just around 6% implemented differential privacy (DP), often without parameter disclosure.

> Event-based EHR data are most commonly targeted, while **continuous and irregular time series
> remain underexplored**.

这一段是你「问题陈述」章节最有力的引证：**该领域对临床真实性与时序依赖关注不足**，
而你的课题正是「虚拟患者**时序数据**生成与**测试**平台」。引文页码请打开 PDF 定位。

---

## 三、无法下载的文献（非开放获取，3 篇）

**未下载是有意为之**：这几篇没有合法免费全文，我不会绕过付费墙。
请通过**学校图书馆**的数据库权限获取。

| 文献 | 获取途径 |
|---|---|
| **Walonoski J, Kramer M, Nichols J, et al. Synthea: An approach, method, and software mechanism for generating synthetic patients and the synthetic electronic health care record. *JAMIA*. 2018;25(3):230-238.** doi:10.1093/jamia/ocx079. PMID:29025144 | JAMIA（Oxford）；PMC 免费阅读：[PMC6016640](https://pmc.ncbi.nlm.nih.gov/articles/PMC6016640/)。**本设计最关键的一篇，建议优先获取** |
| Budu, Etminani, Soliman, et al. Evaluation of synthetic electronic health records: A systematic review and experimental assessment. *Neurocomputing*. 2024;603:128253. doi:10.1016/j.neucom.2024.128253 | Elsevier ScienceDirect（学校图书馆） |
| Ghosheh, Li, Zhu, et al. A Survey of Generative Adversarial Networks for Synthesizing Structured Electronic Health Records. *ACM Computing Surveys*. 2024;56:1-34. doi:10.1145/3636424 | ACM Digital Library（学校图书馆） |

---

## 四、已剔除的检索结果（搜索索引错配，2 条）

这两条是搜索引擎返回的**错误关联**，我最初转述给用户时也是错的，已作废。
**保留记录是为了提醒：切勿从搜索摘要直接引文献。**

| 被误传为 | 实际是 | 判定 |
|---|---|---|
| doi:10.1186/s13040-026-00590-w<br>「用 SNOMED/UMLS 校验生成序列的框架」 | 解析到**完全不同的论文**：*Explainable apprenticeship learning for hierarchical clinical decision modeling with multimodal alignment and safety-aware feedback*（Abdullah, Ather, Fatima），*BioData Mining* | **不得引用** |
| arXiv:2406.09235<br>「TSTR 相关」 | *Variational Mode Decomposition as Trusted Data Augmentation in ML-based **Power System** Stability Assessment*——**电力系统**论文 | **不得引用**；TSTR 请用第 1 篇 |

**未核实 1 条**：中国专利 CN121938538A（「医学知识图谱约束的患者数据生成方法」）。
专利库需专门检索，我未能核到。若要用，请自行到国家知识产权局检索确认。

---

## 五、政府文件与公开数据源（非期刊文献，但需列入参考）

这些是**知识库数据来源**，不是算法文献。均为政府公开文件，可免费获取。

| 来源 | 用途 | 获取 |
|---|---|---|
| 国家卫生健康委《临床路径》（224 病种） | 科室归属、检查项、用药 | 卫健委官网 |
| ICD-10 国家临床版 | 诊断编码与名称 | 卫健委发布 |
| 国家医保药品目录（2024） | 药品通用名与剂型 | 国家医保局官网 |
| 《国家基本公共卫生服务规范（第三版）》 | 慢病随访频次（≥4 次/年 → 中位 91 天） | 卫健委官网 |
| 《中国卫生健康统计年鉴》/ 统计公报 | 就诊率、年龄性别分布 | 卫健委官网 |

---

## 六、还需你自行检索的部分（我搜不到）

**中文期刊文献**：就诊时间分布、候诊时间实测（挂号 10 / 候诊 30 / 面诊 5 / 取药 10 分钟）、
居民就诊率的时间序列分析——这些**必须从 CNKI / 万方 / 维普检索**。
本机的检索通道是 US-only，无法覆盖中文数据库。

建议检索词：

```
门诊就诊时间分布    候诊时间 纵向研究    分级诊疗 就诊人次 时间序列
居民两周就诊率      医院门诊量 季节性     门诊人次 年龄 性别 差异
```

**这些参数目前在设计 §5.3 中标为「需逐条核实」**：要么找到中文文献支撑并改为「有出处」，
要么保留为「假设」并做敏感性分析。**不要**在没找到出处的情况下按文献口吻陈述。

---

## 七、复现下载的命令

```bash
cd docs/references/papers

# arXiv（开放获取）
curl -sSL "https://arxiv.org/pdf/1706.02633" -o Esteban2017_RCGAN_TSTR.pdf
curl -sSL "https://arxiv.org/pdf/1608.00570" -o Kartoun2016_VirtualPatientRepositories.pdf
curl -sSL "https://arxiv.org/pdf/2411.04281" -o Chen2024_ScopingReview_Benchmarking.pdf
curl -sSL "https://arxiv.org/pdf/2510.06267" -o Uppalapati2025_RareGraphSynth_KnowledgeGuided.pdf
curl -sSL "https://arxiv.org/pdf/2604.17114" -o Ahmed2026_ProvenanceGap_TemporalKG.pdf
curl -sSL "https://arxiv.org/pdf/2606.15155" -o Sherafatmandjoo2026_SemanticReasoning_MedicalKG.pdf
curl -sSL "https://arxiv.org/pdf/1604.06651" -o Snoke2018_GeneralSpecificUtility_pMSE_preprint.pdf
curl -sSL "https://arxiv.org/pdf/1712.04078" -o Raab2017_GuidelinesUsefulSyntheticData_preprint.pdf

# ACL Anthology（开放获取）
curl -sSL "https://aclanthology.org/2025.emnlp-main.620.pdf" -o RareSyn2025_EMNLP.pdf

# BMC（开放获取 CC BY）
curl -sSL "https://bmcmedinformdecismak.biomedcentral.com/counter/pdf/10.1186/s12911-025-03326-8.pdf" \
     -o Miletic2025_LongitudinalTimeSeriesReview_BMC.pdf

# Journal of Privacy and Confidentiality（开放获取）
curl -sSL "https://journalprivacyconfidentiality.org/index.php/jpc/article/download/568/551" \
     -o Woo2009_pMSE_GlobalUtilityMeasures.pdf
```

> **Windows 注意**：本机 curl 报 `CRYPT_E_REVOCATION_OFFLINE` 时加 `--ssl-no-revoke`。
> arXiv 的 `http://export.arxiv.org/api/query` 会 301，需 `-L`。

---

## 八、许可状态提醒

本目录的 PDF **全部为开放获取**（arXiv / ACL / BMC CC BY / JPC），可本地保存与引用。
引用时请遵守各自许可，注明出处。

**若日后把本仓库推送到公开平台**（GitHub 等），请确认：
1. 已下载的 11 篇均为 OA，可随仓库分发（建议保留出处）；
2. 付费墙文献**未**放入仓库（当前状态即是如此，请保持）。

相关设计文档：[2026-09-21 医学真实感改造设计](../superpowers/specs/2026-09-21-medical-realism-design.md)

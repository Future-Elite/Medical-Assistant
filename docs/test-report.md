# 模拟 HIS 系统 —— 功能与性能测试报告

- 日期：2026-09-19
- 对应设计文档：`docs/superpowers/specs/2026-09-19-his-simulation-design.md` 第 6 部分「测试与分析」
- 测试对象：模拟 HIS 系统全栈（Spring Boot 后端 + MySQL + Redis + Vue 前端 + 数据生成器）
- 版本：最终修复轮（见 §5「本轮修复与复测记录」）

---

## 0. 测试环境与产物

### 0.1 环境

| 项 | 版本 / 配置 |
|---|---|
| 操作系统 | Windows 11 Pro 10.0.22631 |
| JDK | Java 21.0.8 |
| 后端 | Spring Boot 3.3.5 + MyBatis-Plus 3.5.7，内嵌 Tomcat 10.1.31，端口 8080 |
| 数据库 | MySQL 8，库 `his_demo`，账号 `his_user` |
| 缓存 | Redis（localhost:6379；时间线键 TTL 30 分钟，看板统计键 TTL 10 分钟） |
| 前端 | Vue 3 + Vite（端口 5173） |

### 0.2 被测数据集

由 `his-generator` 全量生成（truncate-before-run），六张业务表实测净产出：

| 表 | 生成器写入行数 | 说明 |
|---|---:|---|
| patient | 10 000 | ≥1 万目标达成 |
| visit | 125 220 | ≥5 万目标达成（2.5 倍） |
| diagnosis | 125 220 | 每次就诊 1 条诊断 |
| prescription | 125 220 | 每次就诊 1 张处方 |
| prescription_item | 179 898 | 每张处方 1–3 条明细，均值 1.437 |
| lab_test | 250 930 | 每张处方 1–3 项检验，均值 2.004 |
| **合计** | **816 488** | = 10 000 + 125 220×3 + 179 898 + 250 930；另有字典表 drug 12 行、rule_config 8 行 |

> 生成器实测 `elapsed_ms = 588 889`（≈9.81 分钟），吞吐 ≈ 1 387 行/秒。

**测量口径说明（重要）**：第 3 节的全部统计量均在**生成器完成之后、功能测试执行之前**测得，
口径为上面的**生成器净产出**。功能测试随后追加了 1 套就诊链（+1 名患者、+1 次就诊及其诊断/处方/检验），
因此测试全部结束时库内实际行数为 **10 001 / 125 221 / 125 221 / 125 221 / 179 900 / 250 931**。
追加量占比 0.001%，不改变第 3 节的任何结论。

### 0.3 测试产物（可复现）

| 文件 | 用途 |
|---|---|
| `backend/his-server/src/test/scripts/functional-test.sh` | 功能测试脚本，逐条执行 12 个用例并打印实测值 |
| `backend/his-server/src/test/java/com/his/server/PerformanceTest.java` | 性能测试脚本，测时间线查询与看板接口的冷/热响应时间 |
| `backend/sql/verify-final.sql` | 数据评估脚本，一次性输出第 3 节的全部完整性 / 时间一致性 / 分布检查 |
| `docs/test-report.md` | 本报告 |

复现方式：

```bash
# 1) 启动 Redis 与后端
redis-server &                                   # 或本地 Redis 服务
cd backend && mvn -q install -DskipTests
mvn -pl his-server spring-boot:run               # 后台运行，等待 8080 就绪

# 2) 数据评估（第 3 节的全部检查）
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t < backend/sql/verify-final.sql

# 3) 功能测试（12 个用例）
bash backend/his-server/src/test/scripts/functional-test.sh

# 4) 性能测试（两种等价入口，需显式开开关，避免污染常规 mvn test）
mvn -pl his-server test -Dtest=PerformanceTest -Dtest.perf.enabled=true

# 独立 main 入口：先用 maven 导出测试 classpath，再用 java 运行（-D 必须放在类名之前）
mvn -q -pl his-server dependency:build-classpath -Dmdep.outputFile=cp.txt -Dmdep.includeScope=test
cd backend/his-server
java -Dstdout.encoding=UTF-8 -Dperf.patientId=1 \
     -cp "target/test-classes;target/classes;$(cat cp.txt)" com.his.server.PerformanceTest
```

复现提示：生成器直接经 JDBC 写库、绕过 service 层，因此**重跑生成器不会失效 `stats:*` / `timeline:*` 缓存**。
复测看板性能前需手动清理，否则会命中上一次数据集留下的旧缓存：

```bash
redis-cli del stats:visit-frequency stats:drug-distribution
```

---

## 1. 功能测试用例执行表

执行方式：脚本对运行中的后端发起真实 HTTP 请求，逐条断言。**12 / 12 全部通过**。

| # | 用例名 | 步骤 | 预期 | 实际 | 结果 |
|---|---|---|---|---|---|
| 1 | 患者建档 | `POST /api/patients`，body：姓名/性别/出生日期/身份证/电话/住址 | HTTP 200，`code=0`，`data.id` 回填自增主键 | HTTP 200，`code=0`，新建患者 `id=10001`，姓名「测试患者063916」 | 通过 |
| 2 | 患者查询-分页 | `GET /api/patients?page=2&size=5` | HTTP 200，`current=2`，返回 5 条，`total ≥ 10000` | HTTP 200，`current=2`，返回 5 条，`total=10001` | 通过 |
| 3 | 患者查询-关键词 | `GET /api/patients?page=1&size=10&keyword=测试患者063916`（中文 URL 编码） | 命中目标患者，`total=1` | 命中 1 条，姓名与关键词一致 | 通过 |
| 4 | 挂号登记 | `POST /api/visits`，关联患者 10001，`visitTime=03:39:17` | HTTP 200，`code=0`，`id` 回填，缓存失效 | HTTP 200，就诊 `id=125221`，`visitTime=2026-09-19T03:39:17` | 通过 |
| 5 | 诊断录入 | `POST /api/diagnoses`，`icdCode=J06.9`，`diagnosedAt` 晚于就诊 | HTTP 200，`code=0` | HTTP 200，诊断 `id=125221`，`icdCode=J06.9`，`diagnosedAt=03:44:17` | 通过 |
| 6 | 处方开具（含明细） | `POST /api/prescriptions`，2 条明细：阿莫西林 18.50×2 盒、感冒灵 12.00×1 盒 | 服务端按药品单价汇总 `totalAmount = 18.50×2 + 12.00×1 = 49.00`，明细落库并挂到处方 | HTTP 200，处方 `id=125221`，明细 2 条，`totalAmount=49.00`（与预期一致） | 通过 |
| 7 | 检验录入 | `POST /api/lab-tests`，`isAbnormal=1` | HTTP 200，`code=0` | HTTP 200，检验 `id=250931`，`isAbnormal=1` | 通过 |
| 8 | 时间线查询（升序） | 先写入就诊/诊断/处方/检验 4 类事件，再 `GET /api/patients/10001/timeline` | 4 个事件按 `time` 严格升序；写入应触发缓存失效并返回最新数据 | 返回 4 个事件（`DIAGNOSIS,LAB_TEST,PRESCRIPTION,VISIT`），时间严格升序，含本次新写入事件，耗时 8.3 ms | 通过 |
| 9 | 时间线查询（生成数据） | `GET /api/patients/1/timeline` | 升序返回该患者全部历史事件 | 返回 38 个事件，时间升序成立 | 通过 |
| 10 | 随机患者 | `GET /api/dashboard/random-patient` | 返回一名真实存在的患者 | HTTP 200，`code=0`，`id=2538`，姓名「刘涛」，耗时 62 ms | 通过 |
| 11 | 就诊频率统计 | `GET /api/dashboard/stats/visit-frequency` | 按月返回就诊量，合计等于总就诊数 | 40 个月份，首月 `2023-06`，合计 **125 221** 次（与 visit 表行数一致），耗时 106 ms | 通过 |
| 12 | 用药分布统计 | `GET /api/dashboard/stats/drug-distribution` | 按明细数降序返回药品，覆盖全部 12 种药品 | 返回 12 种，首位「奥美拉唑肠溶胶囊」27 467 次，耗时 390 ms | 通过 |

### 1.1 用例设计要点与覆盖说明

- **端到端闭环**：用例 1→4→5→6→7 依次建档、挂号、诊断、开方、检验，构成一条完整的门诊业务链；
  用例 8 随即查询该患者时间线，同时验证了「写入 → 缓存失效 → 重新装载」的联动（`CacheInvalidator` 在
  Visit / Diagnosis / Prescription / LabTest 的 `save` 中均调用 `evictTimeline`）。
- **中文链路验证**：用例 3 用中文姓名做关键词检索（姓名 / 身份证 / 电话三字段 OR 匹配），确认
  UTF-8 参数在前后端与数据库三处均未乱码。
- **服务端计算校验**：用例 6 不是简单回显，而是断言服务端依据 `drug.price × quantity` 汇总的
  `totalAmount`，可同时验证明细关联与金额计算逻辑。
- **未覆盖范围**：前端 4 个页面为人工浏览器验收（本报告不展开）；`POST /api/generator/run` 在设计文档中
  标注为「可选」，未实现，故无对应用例。

### 1.2 异常出口验证（本轮新增）

统一异常处理（`GlobalExceptionHandler`）在真实 HTTP 层验证：`POST /api/patients` 分别提交
「违反非空约束的 body」与「畸形 JSON」，两次响应体均为

```json
{"code":500,"message":"服务器内部错误","data":null}
```

且 HTTP 状态码为 200、响应体中**不含** `timestamp`/`status`/`error`/`path` 字段，也**不含**任何异常类名或堆栈文本。
即：Spring 默认错误体已被替换为本项目的 `Result` 信封，前端 axios 拦截器（按 `code !== 0` 判定失败）
能够取到可展示的 `message`，同时不泄露服务端细节。参数校验失败（`MethodArgumentNotValidException` /
`BindException`）走另一分支，返回 `code=400` 与逐字段的可读提示。

---

## 2. 性能测试

### 2.1 批量写入耗时（数据生成器）

由 `his-generator` 全量生成时打印并实测（本轮最终运行）：

| 指标 | 实测值 |
|---|---:|
| 生成规模 | 10 000 名患者 / 125 220 次就诊 |
| 六张业务表合计行数 | 816 488 行（10 000 + 125 220×3 + 179 898 + 250 930） |
| **批量写入耗时 `elapsed_ms`** | **588 889 ms（≈ 9.81 分钟）** |
| 平均吞吐 | ≈ 1 387 行/秒（816 488 ÷ 588.889 s） |

写入采用 MyBatis-Plus 的 JDBC 批量接口，按 500 名患者一个批次（`BATCH_SIZE=500`），沿用自增主键回填
以保证外键取自真实 id；连接串开启 `rewriteBatchedStatements=true` 以合并批量 INSERT。

### 2.2 时间线查询响应时间（缓存前后对比）

被测接口 `GET /api/patients/{id}/timeline`，迭代 20 次、预热 3 次，冷热两轮口径：

- **冷（cache-miss）**：每次迭代前执行 `DEL timeline:{id}`，强制走 4 次数据库查询并重新组装，反映最坏情况；
- **热（cache-hit）**：预热后不再删除，全部命中 Redis 直读。

中位数按「偶数样本取中间两者均值」计算（N=20 时取第 10、11 位的均值，而非直接取第 11 位）。

| 患者 | 事件数 | 响应体 | 轮次 | 最小 | 中位 | **平均** | 最大 | 加速比 |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| 1（典型） | 38 | 9 357 字符 | 冷（miss） | 6 ms | 9.0 ms | **8.6 ms** | 14 ms | — |
| 1 | 38 | 9 357 字符 | 热（hit） | 2 ms | 2.5 ms | **2.6 ms** | 4 ms | **3.4×** |

冷轮样本（患者 1，ms）：`[10,9,9,8,9,10,10,9,10,9,8,8,7,10,7,7,6,6,6,14]`
热轮样本（患者 1，ms）：`[4,3,3,3,3,3,3,3,3,2,2,2,2,2,2,2,2,2,3,2]`

**缓存有效性校验**：脚本对该结果做了**硬断言**——热轮结束后 `EXISTS timeline:1` 必须等于 1，否则用例失败；
同时断言「冷均 > 热均」，以防键名写错导致「热」轮实际仍在走数据库时被误判为通过。
实测 `EXISTS timeline:1 = 1`，键名与 `TimelineServiceImpl.CACHE_PREFIX` 一致。

**结论**：时间线查询在本数据规模下响应为**毫秒级**（冷 8.6 ms、热 2.6 ms），缓存带来 **3.4 倍**加速。

**测量口径说明与波动**：
- 冷路径的绝对耗时依赖 InnoDB 缓冲池状态。本轮冷轮 8.6 ms 是在**生成器刚写完 81.6 万行之后**测得的，
  缓冲池被大量写入冲刷过，属偏悲观的一侧；本报告早期在「缓冲池全热、数据刚生成完」的条件下测得的是
  4 ms 量级。两者共同界定了冷路径的观测区间（约 4–9 ms），结论（毫秒级、缓存加速 3× 上下）不受影响。
- 开发机上偶发 GC/资源竞争会夹带个别离群样本（本轮冷轮出现 14 ms 单点）。因此本报告同时给出
  **中位数**与**样本全量**，而不依赖单轮的算术平均；每轮样本仅 20 次，统计上仅供量级参考。

### 2.3 看板接口耗时（含设计文档第 9 节的统计缓存）

看板三条接口本轮已改为 **SQL 聚合**（`GROUP BY` / `ORDER BY RAND() LIMIT 1`），
并补上了设计文档第 9 节要求的 `stats:visit-frequency` / `stats:drug-distribution` 缓存（TTL 10 分钟）。
迭代 10 次、预热 3 次的实测：

| 接口 | 冷（cache-miss） | 热（cache-hit） | 加速比 | 修复前 | 相对修复前 |
|---|---:|---:|---:|---:|---:|
| `GET /api/dashboard/random-patient` | **3.1 ms**（中位 3.0） | 无缓存（对照组） | — | ≈189–202 ms | **≈61–65×** |
| `GET /api/dashboard/stats/visit-frequency` | **69.4 ms**（中位 70.5） | **0.9 ms**（中位 1.0） | **77.1×** | ≈1 802–1 814 ms | **≈26× / ≈2 000×** |
| `GET /api/dashboard/stats/drug-distribution` | **339.3 ms**（中位 345.0） | **0.6 ms**（中位 1.0） | **565.5×** | ≈2 742–2 757 ms | **≈8.1× / ≈4 600×** |

- **随机患者**（3.1 ms）：旧实现先 `selectList(null)` 把全部 10 000 行载入内存再随机取一个，
  现改为 `SELECT id FROM patient ORDER BY RAND() LIMIT 1` 再 `selectById`，一次主键点查，降幅约 61 倍。
- **就诊频率冷路径**（69.4 ms）：旧实现把 125 220 条就诊全量载入内存分组；现由 MySQL
  `GROUP BY DATE_FORMAT(visit_time,'%Y-%m')` 聚合，只回传 40 行，降幅约 26 倍。
- **用药分布冷路径**（339.3 ms）：聚合与排序（含与 `drug` 的 JOIN）全部下沉到 MySQL，
  只回传 20 行并按 `cnt DESC, d.name ASC` 排序（加药品名次键，保证结果稳定可复现），降幅约 8 倍。
- **缓存命中**（0.9 / 0.6 ms）：两条统计接口的 TTL 实测为 597 s / 600 s，与设计的 10 分钟一致。
  这两条统计的时效性要求低于时间线（数据按天变化），因此长 TTL 无需主动失效即可接受。

**独立复核**：另用 curl 对同一后端做了一轮更大样本的采集（各 10 次冷 / 20 次热），
得到就诊频率冷均 60.1 ms、热均 3.9 ms，用药分布冷均 346.8 ms、热均 2.7 ms，
与上表同量级，结论一致。

### 2.4 缓存降级（Redis 不可用时不影响主流程）

缓存被定位为**纯优化、不是依赖**：Redis 不可用时，读路径退化为直接查库重建，写路径（缓存失效）
只记一条告警日志。`TimelineServiceImpl`、`CacheInvalidator`、`DashboardServiceImpl` 三处的
`opsForValue().get/set` 与 `delete` 均包裹了 `RedisConnectionFailureException` 与兜底 `Exception`。

验证方式：单元测试把 `StringRedisTemplate` mock 成抛异常，断言调用方**不向外传播异常**、
且仍返回正确数据；覆盖 `TimelineServiceTest`（`redis_down_does_not_break_timeline_read`、
`unexpected_redis_error_falls_back_to_rebuild`）、`CacheInvalidatorTest`
（`redis_down_does_not_propagate`、`unexpected_redis_error_does_not_propagate`）、
`DashboardServiceTest`（`stats_degrade_gracefully_when_redis_is_down`）。
本机 Redis 需保持运行（复现环境依赖），因此未做「真实停掉 Redis」的端到端验证，此处如实标注为
**由单元测试覆盖，而非真机故障演练**。

---

## 3. 数据评估

> 统计口径：生成器净产出（10 000 患者 / 125 220 就诊），在功能测试执行前测得。全部检查由
> `backend/sql/verify-final.sql` 一次性输出。

### 3.1 数据完整性

| 检查项 | 结果 |
|---|---|
| `visit.patient_id` 孤儿行 | **0** |
| `diagnosis.patient_id` / `diagnosis.visit_id` 孤儿行 | **0** / **0** |
| `prescription.visit_id` 孤儿行 | **0** |
| `prescription_item.prescription_id` / `.drug_id` 孤儿行 | **0** / **0** |
| `lab_test.visit_id` 孤儿行 | **0** |
| 每名患者的就诊次数落在设计区间 5–20 | 生成数据全部满足（min 5 / max 20 / 均值 12.52，0 例越界） |
| 每张处方的明细条数落在设计区间 1–3 | 实测 min 1 / avg 1.437 / max 3，0 例越界 ✅ |
| 处方金额非空且合理 | min 8.90 / avg 50.63 / max 183.00 元，0 例非正 |
| **诊断 ↔ 用药规则一致性** | **179 898 / 179 898 条明细全部命中 `rule_config` 允许的药品**（越界 0 条） |

**诊断↔用药规则一致性**是本项目「医学逻辑合理性」的核心约束，因此做了逐条校验：
将每条明细沿 `prescription_item → prescription → diagnosis → rule_config(按 icd_code)` 回溯，
用 `FIND_IN_SET(drug_id, rule_config.drug_ids)` 判定，**无一条越界**。

### 3.2 时间一致性

| 检查项 | 结果 |
|---|---|
| 未来时间戳（`visit_time` / `diagnosed_at` / `prescribed_at` / `tested_at` / `patient.created_at` > `NOW()`） | **全部为 0** |
| 同患者就诊时间非严格递增 | **0 例**（`LAG` 逐对比较） |
| 诊断早于就诊（`diagnosed_at < visit_time`） | **0 例** |
| 开方早于诊断（`prescribed_at < diagnosed_at`） | **0 例** |
| 检验早于开方（`tested_at < prescribed_at`） | **0 例** |
| 时间线接口返回升序 | 患者 1（38 事件）、患者 10001（4 事件）均严格升序 ✅ |

生成器的时间链设计：每次就诊按「就诊 → 诊断（+5~19 min）→ 开方（+2~9 min）→ 检验（+20~89 min）」
递进，并对每位患者强制相邻就诊间隔 ≥ 30 分钟。

实测时间范围 `2023-06-20 05:38:27` ~ `2026-09-19 03:57:28`，共 **40 个月份**；
生成数据的单患者病史跨度 min **365** 天 / 平均 **733.1** 天（≈2.0 年） / max **1096** 天，
**0 例越界**，与「1–3 年就诊史」设计精确吻合。

> 本轮已不存在上一版报告中的「唯一一处例外」（患者 10000 的人工遗留行）——生成器以
> truncate-before-run 重跑后，库内不再有绕过生成器写入的历史数据。

### 3.3 统计分布合理性

#### 3.3.1 用药分布：实测 vs 规则模型推算

规则表共 8 条诊断，每位患者每次就诊从 8 条规则中等概率取 1 条（每规则 125 220 ÷ 8 = 15 652.5 次）；
对含 `m` 种药品的规则，取药个数 `k ~ U{1..min(3,m)}` 后随机取 `k` 种，因此某药品被选中的概率为
`p = (1/min(3,m)) · (1/m) · Σ_{k=1..min(3,m)} k`（m=1 → 1.0；m=2 → 0.75；m=3 → 0.667）。
实测对照：

| 药品 | 关联规则（药品数） | 期望概率 | 期望条数 | 实测条数 | 偏差 |
|---|---|---:|---:|---:|---:|
| 奥美拉唑肠溶胶囊 | K29.5(1) + A09.9(2) | 1.75 | 27 391.9 | **27 467** | +0.27% |
| 布洛芬缓释胶囊 | G43.9(1) + R50.9(2) | 1.75 | 27 391.9 | **27 262** | −0.47% |
| 感冒灵颗粒 | J06.9(2) + R50.9(2) | 1.50 | 23 478.8 | **23 314** | −0.70% |
| 格列美脲片 | E11.9(2) | 0.75 | 11 739.4 | **11 837** | +0.83% |
| 二甲双胍片 | E11.9(2) | 0.75 | 11 739.4 | **11 825** | +0.73% |
| 复方甘草口服液 | J20.9(2) | 0.75 | 11 739.4 | **11 780** | +0.35% |
| 左氧氟沙星片 | J20.9(2) | 0.75 | 11 739.4 | **11 755** | +0.13% |
| 蒙脱石散 | A09.9(2) | 0.75 | 11 739.4 | **11 703** | −0.31% |
| 阿莫西林胶囊 | J06.9(2) | 0.75 | 11 739.4 | **11 608** | −1.12% |
| 硝苯地平控释片 | I10(3) | 0.667 | 10 440.0 | **10 482** | +0.40% |
| 苯磺酸氨氯地平片 | I10(3) | 0.667 | 10 440.0 | **10 441** | +0.01% |
| 阿司匹林肠溶片 | I10(3) | 0.667 | 10 440.0 | **10 424** | −0.15% |
| **合计** | | 11.50 | 180 003.8 | **179 898** | **−0.06%** |

**全部 12 种药品实测值相对规则模型期望的偏差都在 ±1.2% 以内，总量偏差 −0.06%**，
说明「诊断 → 用药」的抽样实现与设计意图完全一致，且无系统性偏斜。
排序也合理：被 2 条规则共同开放的药品（奥美拉唑、布洛芬，权重 1.75）居前，
仅由 1 条三药规则开放的药品（硝苯地平、氨氯地平、阿司匹林，权重 0.667）居后。

#### 3.3.2 检验结果分布

| 指标 | 设计 | 实测 |
|---|---:|---:|
| 异常结果占比 `is_abnormal=1` | 15% | **15.04%**（37 736 / 250 930） |
| 检验项目种类 | 5 项（血常规 4 项 + 空腹血糖） | 5 项，每张处方从 5 项中随机取 1–3 条（均值 2.004） |
| 结果值取值方式 | 正常项落在参考区间内，异常项超出上限 | 正常项在 `[low, high]` 内均匀取值；异常项抬到 `high + 0~30%×high` |

#### 3.3.3 患者与就诊分布

| 指标 | 实测 | 评价 |
|---|---|---|
| 患者总数 | 10 000 | 达成 ≥1 万 |
| 就诊记录总数 | 125 220 | 达成 ≥5 万（2.5 倍） |
| 每名患者就诊次数 | min 5 / max 20 / 均值 12.52 | 符合设计 |
| 性别分布 | 男 5 047（50.47%） / 女 4 953（49.53%） | 接近 1:1，无偏斜 |
| 科室分布 | 6 个科室，20 665–20 989 次 | 基本均匀 |
| 医生分布 | 6 位医生，20 807–21 038 次 | 基本均匀 |
| 数据时间跨度 | 2023-06 ~ 2026-09，40 个月份 | 1–3 年设计达成 |

#### 3.3.4 就诊频率分布：单日尖峰已消除 ✅

上一版报告在此处记录了两处结构性偏斜，其中**「末次就诊在最后一天堆积」已在本轮修复**。

**修复内容**：旧实现的每位患者病史窗口右端恒为 `now - 150 min`，且 `visitOffsets` 强制
`offsets[last] = windowMinutes`，于是 **10 000 名患者的最后一次就诊被钉在同一小时内**。
本轮把窗口右端改为**按患者独立随机**：`windowEnd = now - 150 min - U[0, 90] 天 - U[0, 1440] 分钟`，
病史跨度仍由 `windowStart = windowEnd - years` 精确保证为 1–3 年（「末次不晚于 now」的不变量不变）。
末次就诊由此自然散开。

| 日期 | 09-01 | 09-02 | 09-03 | 09-05 | 09-08 | 09-10 | 09-11 | 09-12 | 09-13 | 09-14 | 09-15 | 09-16 | 09-17 | 09-18 | 09-19 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 上一版 | 80 | 78 | 83 | 64 | 18 | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **10 002** |
| **本轮** | 117 | 110 | 124 | 123 | 103 | 116 | 106 | 103 | 112 | 109 | 101 | 111 | 119 | 109 | 23 ※ |

※ 09-19 是本报告的测量日，生成器于当日 **06:36** 运行，受「末次就诊不得晚于 now」约束，
当日可落点的时间窗最晚只到 ≈**04:06**（`DOWNSTREAM_BUFFER_MINUTES = 150`），故该日只覆盖了约 1/6 天，
计数 23 是**当日不完整**造成的偏低尾值，而非数据缺陷——其方向与旧版的尖峰正好相反。

**关键指标对比**：

| 指标 | 上一版 | 本轮 | 结论 |
|---|---:|---:|---|
| 单日最大值 | **10 002**（2026-09-19） | **262**（2026-06-21） | 尖峰消除 |
| 单日峰值 ÷ 基线 | **≈54×**（基线 183.9/日） | **≈1.43×**（基线 183.2/日） | 回到正常波动量级 |
| 连续零就诊天 | **9 天**（09-10 ~ 09-18） | **0 天** | 空档消除 |
| 单小时最大值 | ≈10 001（02 时） | 25（2026-06-23 05 时） | 堆叠消除 |
| 日历天数 / 有就诊天数 | — | **1188 / 1188** | 区间内**每一天**都有就诊 |

> 基线口径：2026-01-01 ~ 2026-08-31 共 243 天、44 522 次就诊 = **183.2 次/日**
> （与上一版报告的 183.9 次/日口径一致，可直接对比）。若取更平稳的平台期
> 2025-09-01 ~ 2026-07-31（335 天、63 643 次 = 190.0 次/日），则比值为 **1.38×**。

**仍然存在的偏斜：日均就诊量逐年递增。** 按年聚合：

| 年份 | 就诊数 | 有就诊天数 | 日均 |
|---|---:|---:|---:|
| 2023（自 06-20 起） | 7 046 | 195 | 36.1 |
| 2024 | 22 476 | 366 | 61.4 |
| 2025 | 49 149 | 365 | 134.7 |
| 2026（至 09-19） | 46 549 | 262 | 177.7 |

根因与旧版相同：病史窗口右端锚定在「生成时刻」，而跨度 `years ∈ {1,2,3}` 随机，
于是最近 1 年叠加了全部三类患者、更早的年份依次只叠加跨度更长的患者，密度天然成 ~3:2:1 的阶梯。
**这是本模型的内生性质，本轮未改动**（改动它需要让「跨度」与「窗口位置」相关，会同时改变
「1–3 年病史」的分布口径）。它表现为一条**单调但连续、无跳变**的爬升曲线，
不影响完整性、时间一致性与接口功能，但确实使 `visit-frequency` 折线在早期偏平、近期偏陡，
属于第 4.2 节中如实保留的改进方向。

**末端衰减（可预期的取舍）**：由于窗口右端不得晚于 `now`，且右端在最近 `TAIL_SPREAD_DAYS = 90`
天内均匀分布，覆盖率必然在这 90 天里线性降到 0——即曲线末端有一段缓降
（2026-08 = 4 226 次，136.3/日；2026-09 前 19 天 = 2 027 次，106.7/日），而非断崖。
这是「不产生未来时间戳」这一硬不变量下的必然结果：**取值越大的 TAIL_SPREAD_DAYS 尖峰越小、缓降越长**。
90 天是本轮实测的折中（曾试 150 天：峰值比 1.52× 但缓降长达约 5 个月，故弃用）。

#### 3.3.5 身份证与出生日期：已合规且三字段自洽 ✅

上一版报告的「身份证 17 位」「出生日期月日恒为生成日」两项缺陷已在本轮修复。
`id_card` 现按 **GB 11643** 生成：6 位地区码（`110101`，北京市东城区） + 8 位出生日期 `YYYYMMDD`
+ 3 位顺序码（**奇数为男、偶数为女**，`gender` 即由该位决定） + 1 位校验位（ISO 7064 mod-11-2，
权重 `7 9 10 5 8 4 2 1 6 3 7 9 10 5 8 4 2`，映射表 `10X98765432`）。
其中 8 位出生日期**直接取自该患者的 `birth_date`**，因此三者天然自洽。

实测（10 000 名患者全量校验，0 例外）：

| 检查项 | 结果 |
|---|---|
| `id_card` 长度 = 18 | **10 000 / 10 000** |
| 地区码前缀 = `110101` | **10 000 / 10 000** |
| `id_card[7..14]` = `DATE_FORMAT(birth_date,'%Y%m%d')` | **10 000 / 10 000** |
| 顺序码奇偶 ↔ `gender` 一致 | **10 000 / 10 000** |
| ISO 7064 校验位正确 | **10 000 / 10 000** |

出生日期分布：

| 指标 | 上一版 | 本轮 |
|---|---|---|
| 月-日取值 | 恒为 `09-19`（10 000 人共享） | **336 种**（`01-01`~`12-28`，不含 29/30/31 日以避免无效日期） |
| 不同出生日期数 | 约 90（仅年份不同） | **8 182 / 10 000** |
| 出生年份范围 | 与 `id_card` 相互独立 | **1937-01-09 ~ 2008-09-19**（与 `id_card` 完全一致） |
| 运行时年龄 | 18–89 岁 | **18–89 岁**（min/max 实测 18 / 89） |

> 说明：年龄沿用项目原有的 **18–89 岁**取值区间，因此出生年份落在 `[1937, 2008]`；
> 生成时对「月日晚于今日」的候选年份额外回退 1 年，确保 `Period.between(birthDate, now)`
> 的整年数仍 ≥ 18（否则年末出生者会短暂落入 17 岁，破坏既有年龄不变量）。
>
> 补充：`patient` 表**没有** `age` 列，年龄不落库、由前端从 `birthDate` 实时推算
> （`Dashboard.vue` / `PatientList.vue` / `PatientDetail.vue` 三处 `ageText(birthDate)`）。
> 因此「年龄 = now − birthDate」是**结构上成立**的——`birthDate` 是年龄的唯一来源，
> 而 `id_card` 内嵌的 `YYYYMMDD` 又等于 `birthDate`，三者不存在可以相互矛盾的第二数据源。

---

## 4. 系统局限与改进方向

### 4.1 性能与容量

| # | 局限 | 现状证据 | 改进方向 |
|---|---|---|---|
| 1 | **批量写吞吐偏低（≈1 387 行/秒）** | 816 488 行耗时 588 889 ms | 提升 `BATCH_SIZE`、改用 `LOAD DATA INFILE`、或按表拆分并行写入；当前单线程逐批事务提交是主要瓶颈 |
| 2 | **单实例、无并发压测** | 本报告全部为单线程串行请求 | 引入 JMeter/wrk 做并发与 P95/P99 分析；补充连接池、多实例部署下的缓存一致性验证 |
| 3 | **用药分布冷路径仍需 339 ms** | 该查询要对 179 898 行明细做 JOIN + GROUP BY，无法靠索引完全消除 | 预聚合物化表（如按日/按药品的汇总表），或改为异步刷新 + 定时任务，使冷路径也只读汇总 |
| 4 | **看板统计的冷路径无并发保护** | `stats:*` 失效瞬间的并发请求会各自全量重算 | 加互斥锁或逻辑过期（同 #8 的时间线防击穿思路） |

### 4.2 数据生成的真实性

| # | 局限 | 现状证据 | 改进方向 |
|---|---|---|---|
| 5 | **日均就诊量逐年递增，时间分布不平稳** | 2023 年 36.1/日 → 2024 年 61.4/日 → 2025 年 134.7/日 → 2026 年 177.7/日 | 让每位患者的病史窗口**起点**随机、跨度独立，而非统一末端锚定；或按目标日均密度做分层抽样 |
| 6 | **末端 90 天覆盖率线性缓降** | 2026-08 = 136.3/日、2026-09 前 19 天 = 106.7/日，低于平台期 190.0/日 | 把「不得晚于 now」的约束改为「按当前时刻截断」——即允许窗口右端更早，由日均密度反推每人就诊次数，使首尾自然；或按人口口径生成后再做时间重采样 |
| 7 | **缺乏真实就诊节律** | 就诊时间在窗口内近似均匀，无季节性、无工作日/周末差异、无节假日效应 | 引入权重模板：呼吸科冬季高发、周一/上午为就诊高峰；按科室设置不同的日/月分布曲线 |
| 8 | **患者与就诊特征过度均匀** | 性别 ≈1:1、6 科室 6 医生基本均匀、每患者 5–20 次就诊近似均匀 | 引入重尾分布（少数慢病患者高频复诊）、重点科室倾斜、医生专长与科室绑定 |
| 9 | **诊断与药品字典规模小** | 仅 8 条诊断规则、12 种药品，且诊断-药品为固定映射 | 扩充 ICD 与药品目录；允许同一诊断按患者特征（年龄/性别/并发症）给出差异化用药 |
| 10 | **无患者维度的纵向连贯性** | 每次就诊的诊断从 8 条规则独立随机抽取，同一患者的多次就诊之间无病程演进 | 引入「主诊断 + 并发症」的病程模型，使后续就诊与既往诊断相关（如高血压患者后续出现糖尿病） |
| 11 | **检验结果与诊断无语义关联** | `newLabTests` 从 5 个项目随机取，与本次诊断无关 | 按诊断决定检验项目（如发热 → 血常规），并让异常值方向与病情一致 |
| 12 | **地址字段为无意义拼接** | `randomName() + "市" + randomName() + "区" + n + "号"`，非真实行政区划 | 使用真实行政区划字典与街道/门牌规则 |

### 4.3 工程与验证

| # | 局限 | 改进方向 |
|---|---|---|
| 13 | **数据库与 Redis 凭据为明文** | `application.yml` 中 `username/password` 明文书写；生产应改为环境变量 / 配置中心 / 密钥管理服务 |
| 14 | **前端无建档表单页面** | 建档目前只有 `POST /api/patients` 接口，前端未提供录入页；需补齐表单与校验 |
| 15 | **缓存失效早于事务提交** | `CacheInvalidator.evictTimeline` 在 `save()` 内、事务提交**之前**调用，极小概率下并发读会把提交前的旧数据回填进缓存；应改为 `TransactionSynchronizationManager` 的 after-commit 回调或延迟双删 |
| 16 | **生成器不失效缓存** | 生成器直接经 JDBC 写库、绕过 service 层，重跑后 `stats:*` / `timeline:*` 会残留旧数据集内容（本次复测靠手动 `redis-cli del`，见 §0.3） | 生成器结束时主动清空本系统的缓存键，或改用 service 层写入 |
| 17 | **性能测试未纳入 CI** | 需运行中的后端与 Redis，故以 `@EnabledIfSystemProperty` 显式开关隔离；应提供 docker-compose 一键起依赖，并在 nightly 流水线中开启该用例 |
| 18 | **冷路径耗时受 InnoDB 缓冲池状态影响** | 当前观测区间约 4–9 ms；应增加缓冲池冷却后的对照测量，或直接以「数据库冷启动后首查」作为冷路径口径 |
| 19 | **功能测试以 shell 脚本断言，未覆盖前端页面** | 前端补 Playwright/Cypress 端到端用例（列表、时间轴、录入表单、看板四页） |
| 20 | **时间线接口无分页/上限保护** | 实测单患者事件数 min 4 / 平均 62.7 / **max 110**（20 次就诊 + 20 诊断 + 20 处方 + 最多 60 项检验），响应约 28 KB；若未来单患者事件数上千需引入分页或按类型过滤 |
| 21 | **缓存无防击穿保护** | 热点患者 `timeline:{id}` 失效瞬间可能出现并发穿透，可加互斥锁或逻辑过期 |
| 22 | **Redis 降级仅由单元测试覆盖** | 未做真机故障演练（复现环境要求 Redis 常驻）；可在 CI 中用容器化的「停/起 Redis」做端到端降级验证 |

### 4.4 总体结论

- **功能**：12 / 12 用例通过，覆盖建档、查询（分页/中文关键词）、挂号、诊断、处方（含明细与金额汇总）、
  检验、时间线升序、随机患者、就诊频率、用药分布，端到端业务闭环成立。
- **性能**：本轮把看板统计从「内存全表聚合」下沉为 **SQL 聚合**，并按设计文档第 9 节补齐
  `stats:*` 缓存（TTL 10 分钟）：随机患者 **189–202 ms → 3.1 ms**（≈61–65×），
  就诊频率 **1 802–1 814 ms → 冷 69.4 ms / 热 0.9 ms**，用药分布 **2 742–2 757 ms → 冷 339.3 ms / 热 0.6 ms**。
  原先的「1.8–2.8 s 性能短板」已消除。时间线查询（核心接口）冷 8.6 ms / 热 2.6 ms，缓存加速 3.4×，
  毫秒级达标；批量写入 816 488 行耗时 588 889 ms（≈1 387 行/秒）。
- **数据质量**：外键完整性 100%（0 孤儿行）、时间一致性 100%（0 未来时间、0 倒序、0 非递增、时间链严格递进）、
  诊断↔用药规则一致性 100%（179 898/179 898）；用药分布与规则模型推算偏差 ≤1.2%，检验异常率 15.04%
  与设计 15% 吻合。**上一版的两类真实性缺陷已修复**：末次就诊单日堆积（10 002 次、54 倍基线 → 峰值 262 次、
  1.43 倍基线、0 个连续零就诊天）与身份证不合规（17 位 → 18 位、地区码/出生日期/性别位/校验位
  四项 10 000/10 000 全部自洽，生日月日 336 种）。
- **仍然成立的主要不足**：就诊量在时间轴上的**逐年递增**（本模型内生性质）、末端 90 天的覆盖率缓降、
  缺乏真实就诊节律与患者纵向病程，以及第 4.3 节的工程项（明文凭据、缓存失效早于事务提交、
  生成器不失效缓存等）。
- 上述结论均由本次实测数据支撑，未使用估算或占位值；所有数据均可按第 0.3 节的方式复现。

---

## 5. 本轮修复与复测记录

本轮针对评审提出的问题做了 8 项修复，并全量重新生成数据、重新测量。

| # | 问题 | 修复 | 复测证据 |
|---|---|---|---|
| 1 | Redis 不可用会让时间线接口 500 | `TimelineServiceImpl` / `CacheInvalidator` 的 Redis 调用全部包裹 `RedisConnectionFailureException` + `Exception`，降级为查库重建 / 仅记日志 | 单元测试 mock 抛异常，断言不传播且数据正确（§2.4） |
| 2 | 看板统计内存全表聚合；设计第 9 节的 `stats:*` 缓存未实现 | 改为 SQL `GROUP BY` / `ORDER BY RAND() LIMIT 1`（新增 3 个 mapper 查询方法）；补齐两个 `stats:*` 键，TTL 10 分钟 | §2.3：随机患者 3.1 ms，统计冷 69.4 / 339.3 ms、热 0.9 / 0.6 ms，TTL 597/600 s |
| 3 | 末次就诊钉死在同一瞬间，造成单日 54 倍尖峰 + 9 天零就诊 | 病史窗口右端改为按患者独立随机（`now - 150min - U[0,90]天 - U[0,1440]min`），跨度仍精确为 1–3 年 | §3.3.4：峰值 262（1.43× 基线）、连续零就诊天 0、单小时峰值 25、1188/1188 天有就诊 |
| 4 | 异常直接抛给容器，返回 Spring 默认错误体，前端拿不到 message | 新增 `GlobalExceptionHandler`（`@RestControllerAdvice`）：兜底 500 + 固定文案「服务器内部错误」，校验失败 400 + 逐字段提示 | §1.2：真实 HTTP 验证，响应体为 `Result` 信封、无堆栈/无默认字段 |
| 5 | 后端无 `spring-boot-maven-plugin`，产不出可执行 jar | `his-server/pom.xml` 补上该插件（写法与 `his-generator/pom.xml` 一致） | `unzip -p target/*.jar META-INF/MANIFEST.MF` 含 `Start-Class: com.his.server.HisServerApplication`；本轮后端即以该 jar 启动 |
| 6 | 身份证 17 位、与出生日期/性别不联动；生日月日恒为生成日 | 按 GB 11643 生成 18 位号码（地区码 + 取自 `birth_date` 的 `YYYYMMDD` + 奇偶定性别的顺序码 + ISO 7064 校验位）；生日改为随机年 + 随机月 + 1..28 日 | §3.3.5：四项一致性均 10 000/10 000；生日月日 336 种、出生年份 1937–2008、年龄 18–89 |
| 7 | `application-generator.yml` 为无用死配置 | 删除 | 全仓检索无引用 |
| 8 | 数据未重新生成、报告未反映现状 | truncate-before-run 全量重跑；用 `verify-final.sql` 重新采集全部统计量并据此重写本报告 | §0.2 / §3 全部数值 |

**本轮验证结果汇总**

| 验证项 | 命令 | 结果 |
|---|---|---|
| 后端单元测试 | `cd backend && mvn -o test` | **36 run / 0 failures / 0 errors**，其中 `PerformanceTest` 2 例按设计 skipped（需显式开关） |
| 性能测试 | `mvn -pl his-server test -Dtest=PerformanceTest -Dtest.perf.enabled=true` | **2 run / 0 failures**（时间线冷热 + 看板冷热） |
| 功能测试 | `bash backend/his-server/src/test/scripts/functional-test.sh` | **PASS=12 FAIL=0** |
| 数据评估 | `mysql ... < backend/sql/verify-final.sql` | 完整性 / 时间一致性 / 分布检查全部通过，详见 §3 |
| 前端回归 | `cd frontend && npm run build` | 构建成功（7.11 s），仅有一条既有的 chunk 体积告警，无源码改动 |

---

## 6. 二期扩展测试（住院时序 + 合理用药/路径校验 + 门诊收费/发药）

> **本节口径声明（重要）**：本节对应二期扩展（`admission` / `medical_order` / `vital_sign` / `charge` /
> `dispense` / `rational_rule` / `clinical_pathway` 七张新表与 `lab_test.admission_id` 增列，
> 生成器住院时序与违规注入，`/api/validation/run` 一键校验，看板 3 个新统计接口，住院/校验两个前端页面）。
> 观测日期：**2026-09-21**。
>
> **本次未重跑生成器、未做数据库级复测**，因此本节只写两类可落地的事实：
> **(a)** 代码中的设计常量与由这些常量直接推导的期望值（推导过程全部写出，可逐行核对）；
> **(b)** 本节**实际执行过**的测试输出（§6.5）。
> 凡需「生成器全量重跑 + MySQL 断言」或「性能基准」才能得到的数值，一律标注为「**待实测**」并给出命令，
> 不给出任何估算值冒充实测值。

### 6.0 本节涉及的设计常量（源码取值，可核对）

| 常量 | 取值 | 定义位置 | 含义 |
|---|---:|---|---|
| `INPATIENT_RATIO` | `0.2` | `DataGeneratorService.java:60` | 有住院史的患者比例（20%） |
| `MIN_ADMISSIONS` / `MAX_ADMISSIONS` | `1` / `3` | 同上 `:62–63` | 每名住院患者的住院次数区间 |
| `MIN_STAY_DAYS` / `MAX_STAY_DAYS` | `3` / `15` | 同上 `:65–66` | 单次住院天数区间（`nextInt(3, 16)`，闭区间 3–15） |
| `PATHWAY_COMPLIANCE_RATE` | `0.7` | 同上 `:68` | **住院级别**的临床路径合规比例（30% 变异） |
| `VIOLATION_RATE` | `0.03` | 同上 `:58` | 处方级合理用药违规注入比例（3%） |
| `TAIL_SPREAD_DAYS` | `90` | 同上 `:46` | 门诊病史窗口右端的末段散开天数（一期遗留参数，本节沿用） |
| 收费时间 | `visit_time + U[125,155) 分钟` | `newCharge` `:492` | 每就诊 2–3 条收费（挂号费 + 药费 + 有检验时的检验费） |
| 发药时间 | `visit_time + U[165,205) 分钟` | 主循环 `:249` | 每张处方 1 条发药，**恒晚于该就诊全部收费**（165 > 155） |

### 6.1 住院数据完整性

**生成语义（源码事实）**：每名被抽中住院的患者生成 1–3 次住院，住院号 `A{patientId}-{序号}`；
首次住院锚在**该患者末次门诊之后**，后续每次在上一次住院之后至少间隔 `MAX_STAY_DAYS`（15）天，
从而「同患者多次住院严格升序且互不重叠」是**构造性成立**的。
每次住院内生成：长期医嘱 1–3 条（`admission_time + U[5,60) min`，`status=0`）、
每日临时医嘱（每天 1/3 概率，`+day 天 +9h`，`status=2`）、每日检验（每天 1/2 概率，`+day 天 +8h`）、
每日 3 次生命体征（`+day 天`，时刻 `6 + slot×6 + U[0,1)` 时，即 06/12/18 时前后）、
路径合规时补齐 `LAB`/`MED` 步骤事件；出院时间 `admission_time + totalDays 天 + U[0,24) 小时`，
`total_cost = deposit + totalDays × 120`，`status=1`。
`AdmissionController` 的 `POST /api/admissions` 直接落库 `Admission` 实体，**无服务端补默认值**，
因此前端表单必须自带 `patient_id` / `admission_no` / `admission_time` 三个 NOT NULL 列（见 §6.5 改造说明）。

**检查项与结果**：

| # | 检查项 | 期望 | 实测 |
|---|---|---|---|
| 1 | 规模：`admission` / `medical_order` / `vital_sign` / `charge` / `dispense` 行数 | 非 0 且与门诊规模量级相称 | **待实测** |
| 2 | `medical_order` / `vital_sign` / `lab_test` 的 `admission_id`、`patient_id` 孤儿行 | **0** | **待实测** |
| 3 | 医嘱/体征时间早于入院（`start_time` / `recorded_at` < `admission_time`） | **0** | **待实测** |
| 4 | 医嘱/体征时间晚于出院（> `discharge_time`） | **0** | **待实测** |
| 5 | `discharge_time` ≤ `admission_time` | **0** | **待实测** |
| 6 | 同患者多次住院重叠（`a2.admission_time` 落在 `a1` 住院区间内） | **0** | **待实测** |
| 7 | 同患者住院时间严格升序（`LAG` 逐对比较） | **0 例非递增** | **待实测** |
| 8 | 住院相关表无未来时间戳 | **0** | **待实测** |
| 9 | `dispense.dispensed_at` 早于或等于同就诊任一 `charge.charged_at` | **0** | **待实测** |
| 10 | 每就诊收费条数 ∈ {2, 3}、每处方发药条数 = 1 | 全部满足 | **待实测** |
| 11 | 住院时间轴 `GET /api/admissions/{id}/timeline` 的 `ORDER` / `VITAL_SIGN` / `LAB_TEST` 事件按 `time` 升序 | 严格升序 | **待实测** |

**复现命令**（全量重跑生成器取得干净数据集后执行；新库需先 `schema.sql` + `seed.sql` + `seed-extension.sql`）：

```bash
# 1) 全量重跑（TRUNCATE 十一张业务表后重建，含住院/收费/发药；同时打印 rows 与 elapsed_ms）
cd backend && mvn -q -pl his-generator -am install -DskipTests
java -jar his-generator/target/his-generator-1.0.0.jar
#    输出示例：{"patients":10000,...,"admissions":N,"orders":N,"vital_signs":N,"charges":N,"dispenses":N,"elapsed_ms":M}

# 2) 完整性 / 时间一致性断言（逐条应为 0 或落在期望区间）
mysql -uhis_user -p'his@2026' -D his_demo --default-character-set=utf8mb4 -t <<'SQL'
SELECT '规模' AS section;
SELECT 'admission' AS t, COUNT(*) AS rows_ FROM admission
UNION ALL SELECT 'medical_order', COUNT(*) FROM medical_order
UNION ALL SELECT 'vital_sign',    COUNT(*) FROM vital_sign
UNION ALL SELECT 'charge',        COUNT(*) FROM charge
UNION ALL SELECT 'dispense',      COUNT(*) FROM dispense;

SELECT '孤儿行（应全为 0）' AS section;
SELECT 'order.admission_id' AS fk, COUNT(*) AS orphans FROM medical_order o LEFT JOIN admission a ON a.id=o.admission_id  WHERE a.id IS NULL
UNION ALL SELECT 'order.patient_id',   COUNT(*) FROM medical_order o LEFT JOIN patient p ON p.id=o.patient_id WHERE p.id IS NULL
UNION ALL SELECT 'vital.admission_id', COUNT(*) FROM vital_sign v  LEFT JOIN admission a ON a.id=v.admission_id WHERE a.id IS NULL
UNION ALL SELECT 'vital.patient_id',   COUNT(*) FROM vital_sign v  LEFT JOIN patient p ON p.id=v.patient_id WHERE p.id IS NULL
UNION ALL SELECT 'lab.admission_id',   COUNT(*) FROM lab_test l    LEFT JOIN admission a ON a.id=l.admission_id WHERE l.admission_id IS NOT NULL AND a.id IS NULL;

SELECT '时间一致性（应全为 0）' AS section;
SELECT '医嘱早于入院' AS c, COUNT(*) AS n FROM medical_order o JOIN admission a ON a.id=o.admission_id WHERE o.start_time   < a.admission_time
UNION ALL SELECT '体征早于入院', COUNT(*) FROM vital_sign v JOIN admission a ON a.id=v.admission_id WHERE v.recorded_at < a.admission_time
UNION ALL SELECT '医嘱晚于出院', COUNT(*) FROM medical_order o JOIN admission a ON a.id=o.admission_id WHERE o.start_time   > a.discharge_time
UNION ALL SELECT '体征晚于出院', COUNT(*) FROM vital_sign v JOIN admission a ON a.id=v.admission_id WHERE v.recorded_at > a.discharge_time
UNION ALL SELECT '出院不晚于入院', COUNT(*) FROM admission WHERE discharge_time IS NOT NULL AND discharge_time <= admission_time
UNION ALL SELECT '同患者住院重叠', COUNT(*) FROM admission a1 JOIN admission a2
      ON a1.patient_id = a2.patient_id AND a1.id <> a2.id
     AND a2.admission_time > a1.admission_time AND a2.admission_time < a1.discharge_time
UNION ALL SELECT '同患者住院非升序', COUNT(*) FROM (
      SELECT patient_id, admission_time,
             LAG(admission_time) OVER (PARTITION BY patient_id ORDER BY admission_time) AS prev
      FROM admission) x
      WHERE prev IS NOT NULL AND admission_time <= prev
UNION ALL SELECT '未来时间戳', (SELECT COUNT(*) FROM admission     WHERE admission_time > NOW())
      + (SELECT COUNT(*) FROM medical_order WHERE start_time  > NOW())
      + (SELECT COUNT(*) FROM vital_sign    WHERE recorded_at > NOW())
      + (SELECT COUNT(*) FROM charge        WHERE charged_at  > NOW())
      + (SELECT COUNT(*) FROM dispense      WHERE dispensed_at > NOW());

SELECT '门诊收费/发药时序（应全为 0 / 全满足）' AS section;
SELECT '发药不晚于收费' AS c, COUNT(*) AS n FROM dispense d JOIN charge c ON c.visit_id=d.visit_id WHERE d.dispensed_at <= c.charged_at
UNION ALL SELECT '每就诊收费条数越界', COUNT(*) FROM (
      SELECT visit_id, COUNT(*) c FROM charge GROUP BY visit_id HAVING c NOT IN (2,3)) y
UNION ALL SELECT '每处方发药条数<>1', COUNT(*) FROM (
      SELECT prescription_id, COUNT(*) c FROM dispense GROUP BY prescription_id HAVING c <> 1) z;
SQL

# 3) 住院时间轴升序与响应体（对任一真实 admission_id，例如 1）
curl -s "http://localhost:8080/api/admissions/1/timeline" | head -c 400
```

**说明**：以上 SQL 是本轮新写的断言，**尚未执行**；§6.1 表格中的 11 项因此全部标注「待实测」。
一期报告 §0.3 的 `backend/sql/verify-final.sql` 经核对**不含任何二期表的检查**
（`grep -ci "admission\|vital_sign\|charge\|dispense" verify-final.sql` = `0`），
故二期数据评估需要上述新断言，建议后续并入该脚本形成 `verify-extension.sql`。

### 6.2 体征趋势合理性

体温采用「先升后降」的病程模型（`DataGeneratorService.newVitalSign` `:610–611`）：

```java
double trend = day < 2 ? 0.6 - day * 0.2 : 0.0;   // day0 = 0.6，day1 = 0.4，day2 起 = 0
double temp  = 36.5 + trend + rnd.nextDouble() * 0.6;
vs.setTemperature(BigDecimal.valueOf(Math.round(temp * 10) / 10.0));   // 保留 1 位小数
```

| 住院日 `day` | `trend` | 体温取值区间 | 期望均值 |
|---:|---:|---|---:|
| 0（入院当日） | 0.6 | `[37.1, 37.7)` | ≈ 37.4 ℃ |
| 1 | 0.4 | `[36.9, 37.5)` | ≈ 37.2 ℃ |
| ≥ 2 | 0.0 | `[36.5, 37.1)` | ≈ 36.8 ℃ |

即住院初期呈现低热（37–37.7 ℃，符合感染/炎症急性期），第 2 天起回落到正常区间（36.5–37.1 ℃），
「先升后降」的单调回落由**区间整体平移**实现，而非对单个患者做随机游走——
因此该趋势在**群体均值**上可检出，而在**个体**上表现为逐日下采样。
其余体征为围绕成人正常值的小幅抖动：脉搏 `72 ± 8`、呼吸 `16 ± 2`、收缩压 `120 ± 12`、
舒张压 `78 ± 8`（`rnd.nextInt(-k, k+1)`，即边界均可达）。

**待实测的验证方式**（群体层面，不依赖人工观察）：

```sql
-- 按住院日聚合体温均值，应看到 day0 > day1 > day2+ 的单调回落
SELECT TIMESTAMPDIFF(DAY, a.admission_time, v.recorded_at) AS day_index,
       COUNT(*) AS n, ROUND(AVG(v.temperature), 3) AS avg_temp,
       MIN(v.temperature) AS min_temp, MAX(v.temperature) AS max_temp
FROM vital_sign v JOIN admission a ON a.id = v.admission_id
GROUP BY day_index HAVING day_index BETWEEN 0 AND 6 ORDER BY day_index;

-- 全部体征应落在人体生理合理区间（防止抖动越界）
SELECT MIN(temperature), MAX(temperature), MIN(pulse), MAX(pulse),
       MIN(systolic_bp), MAX(systolic_bp) FROM vital_sign;
```

> 注意：`day=0` 的三条体征中，落在入院时刻**之前**的记录会被生成器丢弃
> （`if (!vs.getRecordedAt().isBefore(a.getAdmissionTime()))`），因此 `day_index=0` 的样本数
> 会少于 `3 × 住院数`；这是设计行为，不构成缺陷。上述两条查询均**待实测**。

### 6.3 校验报告准确性（合理用药率 / 路径执行率）

被测接口 `POST /api/validation/run` 返回 `ValidationReport{ rationalDrug, pathway }`。

**（1）合理用药率：注入比例 ≠ 检测比例（本节最主要的发现）**

`RationalDrugValidator` 逐处方检查三类规则：`INDICATION`（适应症，药品不在该诊断的 `rule_config`
允许集内）、`INTERACTION`（相互作用对）、`DOSE`（日剂量上限）。
先把可由代码**严格推导**的三项写清楚：

| 规则类型 | 自然数据能否触发 | 推导 |
|---|---|---|
| `INDICATION` | **不能** | 生成器的自然处方明细全部取自该诊断的 `rule_config.drug_ids`（一期报告 §3.1 已实测 179 898/179 898 命中），只有注入才会替换为规则外药品 |
| `DOSE` | **不能** | 校验器算 `parseInt(dosage) × parseInt(frequency)`；生成器给药 `"1片"` + `"每日1次"`/`"每日2次"` → 日剂量 1 或 2，而 `rational_rule` 的两条剂量上限均为 `8.00`。注入时改写为 `"4片"` + `"每日3次"` → 12 > 8，可稳定触发 |
| `INTERACTION` | **能（且量不小）** | 见下 |

`INTERACTION` 的自然触发来自 `I10`（原发性高血压）这一条规则：其 `rule_config.drug_ids = '4,9,10'`
（4 硝苯地平控释片 / 9 阿司匹林肠溶片 / 10 苯磺酸氨氯地平片），而 `rational_rule` 中存在相互作用对
`(4,10)`——「两种钙通道阻滞剂重复用药」。生成器取药方式是
`k ~ U{1..min(3,m)}` 后从该规则的 `m` 种药里**无放回随机取 k 种**（`pickDrugIds` `:748–753`），
故单张 `I10` 处方同时含 4 与 10 的概率为

```
P = P(k=2)·(1/C(3,2)) + P(k=3)·1 = (1/3)(1/3) + (1/3)(1) = 0.1111 + 0.3333 = 0.4444
```

按 8 条诊断规则均分（一期实测 125 220 张处方 / 8 ≈ 15 652 张），自然命中相互作用规则的处方约
`0.4444 × 15 652 ≈ 6 957` 张，占全部处方 **≈ 5.6%**。另外两组相互作用对
`(1,8)` 阿莫西林↔左氧氟沙星、`(3,9)` 布洛芬↔阿司匹林 的药物分属不同诊断规则，自然数据**不可能**同时出现。

| 口径 | 值 | 性质 |
|---|---:|---|
| 注入比例 `VIOLATION_RATE` | 3.0% | 源码常量 |
| 名义「合理用药率」（仅按注入比例） | **97.0%** | 设计期望 |
| 校验器额外命中的自然违规（`(4,10)` 相互作用） | **≈ 5.6%** | 由上表推导 |
| 两者重叠（注入与自然同处方） | ≈ 0.2% | 推导，量级可忽略 |
| **推导合理用药率** | **≈ 91%** | 推导 → 与 97% 相差约 6 个百分点 |
| 实测合理用药率 | **待实测** | 见下方命令 |

**结论**：`PATHWAY_COMPLIANCE_RATE` / `VIOLATION_RATE` 这类参数刻画的是**生成器侧的注入/合规比例**，
而报告接口输出的是**校验器侧的检出比例**，两者只有在「自然数据不会触发任何规则」时才相等。
本节证明 `INTERACTION` 类型不满足该前提，因此报告中的合理用药率**预计明显低于 97%**。
这既可能是生成器的缺陷（`I10` 合法处方不应同时开出两种 CCB，应与 `A09.9`/`K29.5` 等一样避免
规则冲突组合），也可能是报告口径问题（`rationalRate` 应拆分为「注入违规检出」与「自然违规检出」两项）。
**建议在写论文前先按下列命令实测一次，再把该结论写死。**

```bash
# 需后端 + Redis 运行中；首次调用为冷路径（全表扫描 12.5 万处方）
curl -s -X POST http://localhost:8080/api/validation/run > /tmp/validation.json
python -c "import json;d=json.load(open('/tmp/validation.json'))['data'];r=d['rationalDrug'];p=d['pathway'];print('total',r['totalPrescriptions'],'violations',r['violationPrescriptions'],'rationalRate',r['rationalRate']);print('enrolled',p['enrolledAdmissions'],'steps',p['totalSteps'],'fulfilled',p['fulfilledSteps'],'executionRate',p['executionRate'])"
# 违规类型分布（确认自然违规确实集中在 INTERACTION）
python -c "import json,collections;d=json.load(open('/tmp/validation.json'))['data'];print(collections.Counter(v['ruleType'] for v in d['rationalDrug']['violations']))"
```

**（2）路径执行率：住院级合规 ≠ 步骤级执行率**

`PathwayReport.executionRate = fulfilledSteps / totalSteps`，是**步骤级**指标；
而 `PATHWAY_COMPLIANCE_RATE = 0.7` 是**住院级**的「是否补齐路径事件」开关。两者不等价，
差异集中在 `DISCHARGE` 步骤上：

- 校验器对 `DISCHARGE` 的判定是 `discharge_time ∈ [admission_time + day 天, +day 天 + window 小时]`
  （`PathwayValidator` `:63–65`）；两条路径的 `DISCHARGE` 步骤分别是 `day=3 window=24`（J06.9）与
  `day=5 window=24`（E11.9），即要求出院时间恰好落在第 3 或第 5 个住院日内。
- 生成器的住院天数 `totalDays ~ U{3..15}`（13 个取值）**独立于路径**，
  出院时间为 `admission_time + totalDays 天 + U[0,24) 小时`，
  因此 `DISCHARGE` 步骤命中的充要条件是 `totalDays == day`，概率仅 `1/13 ≈ 0.077`。
- 合规住院的 `LAB`/`MED` 步骤由 `labTestForItem`（`+day 天 + U[1,window) 小时`）与医嘱
  （`+day 天 + 10 小时`）生成，**必然**落在窗口内；且生成时有 `step.day() >= totalDays → continue`
  的守卫，会让「步骤日超出住院时长」的步骤直接缺事件。

由此推导（`STEP_HIT` 为步骤命中数）：

| 路径 | 步骤数 | 合规住院的期望 `STEP_HIT` | 期望执行率 |
|---|---:|---:|---:|
| J06.9 上呼吸道感染 | 3（LAB+MED+DISCHARGE） | `1 + 1 + 1/13 = 2.077` | 0.692 |
| E11.9 2 型糖尿病 | 4（LAB+MED+LAB+DISCHARGE） | `1 + 1 + 1 + 1/13 = 3.077` | 0.769 |
| 两条路径等概率 | — | — | **0.731** |

变异住院（30%）仍可能被随机日常事件偶然命中（校验器不看「是否补齐」，只看事件是否落在窗口内），
粗估每步骤 0.08–0.20。合并后：

| 口径 | 值 | 性质 |
|---|---:|---|
| `PATHWAY_COMPLIANCE_RATE`（住院级） | 70.0% | 源码常量 |
| 推导步骤级执行率 | **≈ 55%**（`0.7 × 0.731 + 0.3 × ≈0.12`） | 推导 |
| 实测路径执行率 | **待实测** | 见上方命令 |

**结论**：`executionRate ≈ 70%` 这一预期**不成立**，预计落在 50–60% 区间；
主因是 `DISCHARGE` 步骤的窗口与 `totalDays` 的均匀分布近乎独立。
若希望报告中的执行率贴近 0.7，需让 `totalDays` 与路径的出院日挂钩
（合规住院把 `totalDays` 设为路径 `DISCHARGE` 步骤的 `day`），或把 `DISCHARGE` 步骤的判定
放宽为「出院时间晚于该步骤下界」即可。以上均为**推导**，须按本节上文（1）的命令实测确认后才可写入论文。

### 6.4 性能（全部待实测）

本节三项均需重跑生成器或运行压测脚本，**本次未执行**，故只给出口径与命令，不给任何数值。
口径与一期一致：迭代次数与预热次数沿用 §2.2 的「迭代 20 次 / 预热 3 次」，
中位数按「偶数样本取中间两者均值」计算。

| # | 指标 | 测量口径 | 命令 | 结果 |
|---|---|---|---|---|
| 1 | 住院批量写入耗时 | 生成器全量重跑后输出的 `elapsed_ms`（含全部 11 张表的批量写入；一期 6 张表实测 588 889 ms 可作对照基线） | `java -jar his-generator/target/his-generator-1.0.0.jar`，读取输出 JSON 的 `elapsed_ms` 与 `admissions` / `orders` / `vital_signs` 计数 | **待实测** |
| 2 | 住院时间轴查询延迟 | `GET /api/admissions/{id}/timeline`；**冷**轮每次迭代前 `DEL timeline:admission:{id}`（键名与 `TimelineServiceImpl.ADMISSION_CACHE_PREFIX` 一致，TTL 30 分钟），**热**轮预热后连读。响应体为 `ORDER`/`VITAL_SIGN`/`LAB_TEST` 三类事件的升序数组 | 见下方脚本（一期的 `PerformanceTest.java` 只覆盖患者时间线与看板统计，**尚未覆盖**该接口与 `/api/validation/run`） | **待实测** |
| 3 | 校验引擎扫描耗时 | `POST /api/validation/run` 的端到端响应时间；引擎需全量载入 125 220 张处方 + 179 898 条明细 + 250 930 项检验，并为每条路径同时命中做 3–4 次逐步窗口判定 | `curl -w '%{time_total}s\n' -s -o /dev/null -X POST http://localhost:8080/api/validation/run`（首次为冷路径） | **待实测** |
| 4 | 看板 3 个新统计接口 | `GET /api/dashboard/stats/{admission-ratio,rational-drug-rate,pathway-rate}`，各自带 `stats:*` 缓存（TTL 10 分钟，见 `DashboardServiceImpl:51–54`） | 复测前先 `redis-cli del stats:admission-ratio stats:rational-drug-rate stats:pathway-rate`（沿用 §0.3 的提示：生成器绕过 service 层，**不会**失效这些缓存） | **待实测** |

```bash
# 住院时间轴冷热两轮（替换 ADMISSION_ID 为任一真实住院 id；Redis 需可用）
ADMISSION_ID=1
redis-cli del "timeline:admission:$ADMISSION_ID" >/dev/null
for i in $(seq 1 20); do
  redis-cli del "timeline:admission:$ADMISSION_ID" >/dev/null
  curl -o /dev/null -s -w 'cold %{time_total}s\n' "http://localhost:8080/api/admissions/$ADMISSION_ID/timeline"
done
for i in $(seq 1 20); do
  curl -o /dev/null -s -w 'hot  %{time_total}s\n' "http://localhost:8080/api/admissions/$ADMISSION_ID/timeline"
done
```

> **对一期性能结论的影响**：本期新增的住院/校验接口尚未纳入性能脚本，
> 因此 §2 的一期结论（时间线冷 8.6 ms、看板统计热 0.9/0.6 ms 等）**不覆盖**这些新接口，
> 不能直接外推。

### 6.5 本节实际执行的验证结果

以下两项是本节**真实执行**的输出，可直接复现。

| 验证项 | 命令 | 实际结果 |
|---|---|---|
| 后端单元测试（含二期全部新增用例） | `cd backend && mvn -o test` | **BUILD SUCCESS**；`his-server` **54 run / 0 failures / 0 errors / 2 skipped**，`his-generator` **21 run / 0 failures / 0 errors / 0 skipped**（合计 75 run / 0 失败，跳过的是 `PerformanceTest` 的 2 例显式开关用例）。总耗时 11.9 s |
| 前端构建 | `cd frontend && npm run build` | **构建成功（7.07 s）**，产物生成；仅剩既有的 chunk 体积告警（`Chart-*.js` 1 035.56 kB、`index-*.js` 1 052.94 kB 超过 500 kB 阈值），无编译/ESLint 错误 |

二期相关的通过用例（逐类实测，均为 0 失败）：

| 用例集 | run | 覆盖内容 |
|---|---:|---|
| `AdmissionControllerTest` | 1 | `POST/GET /api/admissions`、出院、时间轴（切片测试） |
| `ChargeControllerTest` / `DispenseControllerTest` | 1 / 1 | 收费与发药落库 |
| `VitalSignControllerTest` / `MedicalOrderControllerTest` | 1 / 2 | 体征与医嘱接口 |
| `ValidationControllerTest` | 1 | `POST /api/validation/run` 返回信封 |
| `RationalDrugValidatorTest` / `PathwayValidatorTest` | 3 / 2 | 合理用药三类规则、路径步骤命中与变异 |
| `AdmissionServiceImplTest` | 2 | 住院服务（含出院回写） |
| `DashboardServiceTest` | 10 | 含 3 个新统计接口与 Redis 降级 |
| `DataGeneratorServiceTest` | 21 | 含 `inpatient_patients_get_admissions_with_strictly_increasing_events`（断言医嘱/体征时间 ≥ 入院时间且 < 出院时间、出院时间 > 入院时间）、`generates_charges_and_dispenses_per_visit`、`injectViolation_produces_a_known_violation` |

> 说明：上表的 0 失败是**单元/切片测试**结论（Mapper 多为 mock，不连真实 MySQL），
> 它验证的是「代码分支正确」，**不能**替代 §6.1 的数据库级完整性断言。
> §6.1 的 11 项、§6.2 的 2 条查询、§6.3 的两个比例、§6.4 的 4 项性能均**待实测**。

**本节相应的前端改造**（与上述测试同批提交）：
`Dashboard.vue` 在图表行之前新增「住院占比 / 合理用药率 / 路径执行率」三张指标卡，
`loadStats()` 由 2 路并发扩为 5 路并发，任一接口失败只让对应卡片显示 `-`（不抛错、不阻断其余内容），
字段绑定为 `ratio` / `rationalRate` / `executionRate`；
`EntryForm.vue` 新增「住院登记 / 收费 / 发药」三个**独立表单** tab（直接填患者 ID，不依赖顶部医生/患者上下文），
并针对后端 NOT NULL 列做了必填校验（住院：`admissionNo` / `admissionTime`；收费：`visitId` / `chargeNo` / `amount` / `chargedAt`；
发药：`visitId` / `prescriptionId` / `dispensedAt`），三个提交函数均复用既有的 `submit()` 助手
（统一 `ElMessage` 提示 + `lastPatientId` 回填），避免与一期四个 tab 的行为分叉。

### 6.6 本节小结

- **本次真正跑通的**：后端 75 例单元/切片测试 0 失败；前端构建成功、无错误。这两项构成本节的全部实测证据。
- **本次未跑、已如实标注「待实测」的**：住院数据完整性 11 项、体征趋势 2 条查询、
  合理用药率与路径执行率两侧比值、4 项性能指标——均已给出可直接执行的 SQL / 命令，复现口径与一期 §0.3 一致。
- **两处需要作者决策的口径问题（均由代码推导得出，实测后应写进论文的「局限」）**：
  1. 「注入比例 ≠ 检出比例」：合理用药率预计 **≈91%** 而非 97%，差额来自生成器合法产出的
     `I10` 处方可同时含硝苯地平与氨氯地平，被 `INTERACTION` 规则（正确地）判为违规；
  2. 「住院级合规 ≠ 步骤级执行率」：路径执行率预计 **≈55%** 而非 70%，主因是 `DISCHARGE` 步骤
     的 24 小时窗口与 `totalDays ~ U{3..15}` 近乎独立，命中概率仅 1/13。
  两项都不是「报告造假」，而是**指标口径与生成器参数之间的错位**；修法已在上文分别给出。

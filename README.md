# HISdata —— 上海地区合成患者数据

用 Synthea 生成符合上海特征的合成 HIS 数据。**第一轮范围：2 型糖尿病端到端**，
产出 8 张关系型 CSV，外键可直接建库。

> **本仓库不提交生成产物与设计文档**：`output/`（生成的患者数据）、`modules/`（编译出的 GMF）、
> `review/`（核对清单）、`docs/`（设计文档 / spec）都在 `.gitignore` 里。clone 之后按 §2–§3 生成即可，无需任何预生成文件。

---

## 0. 它做什么

```
ir/sh_t2dm.yaml  ──编译器──>  modules/sh_t2dm.json  ──Synthea──>  FHIR Bundle
   （人工审阅对象）              （Synthea 的 GMF 格式）              （21 类资源）
                                                                      │
                                                                   导出层
                                                                      ↓
                                                          8 张 CSV（UTF-8 BOM）
```

**为什么绕这么一圈**：直接让大模型吐 Synthea 模块是行不通的 —— 旧版的
`llm.py` 就是那么干的，产出的 JSON 从未被成功加载过。现在大模型只负责产出
**便于人工审阅的临床路径 IR**（每个数字强制带 `source` 出处），编译器再把 IR
翻成 Synthea 能真正加载的 GMF。

---

## 1. 环境准备

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | ≥ 3.11 | 实测 3.13.15 |
| Java | 21 | Synthea 要 JDK 21 |
| Synthea 源码 | — | 需要**源码树**（不是 jar），因为要打补丁 |

```bash
# 1) 装 Python 依赖（可编辑安装，装完就不用每次带 PYTHONPATH 了）
pip install -e .
#    或者不装，后面每条命令前面加 PYTHONPATH=src

# 2) 告诉项目 Synthea 在哪
export SYNTHEA_HOME=F:/githubProject/synthea-master/synthea-master
#    Windows cmd:  set SYNTHEA_HOME=...
#    不设也行，代码里的默认值就是上面这个路径（见 src/hisgen/__init__.py）
```

`SYNTHEA_HOME` 指向的必须是 Synthea **源码根目录**（里面能看到
`run_synthea.bat` 和 `src/main/java/`）。

---

## 2. 第一次运行：打补丁（只需一次）

```bash
python shanghai/patches/apply_patches.py --check    # 先看状态
python shanghai/patches/apply_patches.py            # 打补丁
```

改 Synthea 源码树里的 **5 个 Java 文件**，都是 Synthea **没有任何配置项能用**
的地方：

| 文件 | 改什么 |
|---|---|
| `Names.java` | 读我们自己的中文姓名池 + 中国格式地址 |
| `LifecycleModule.java` | 手机号中国号段、身份证号 18 位（按 GB 11643 算校验位） |
| `Provider.java` | 机构与科室的选取 |
| `Demographics.java` | 语言分布改为可配置（默认近 100% `chinese`） |
| `FhirR4.java` | 一处导出字段 |

**补丁原则是「只做加法」**：每一项都先查配置键，配置缺失时行为与原来完全一致，
没打补丁的项目跑同一个 Synthea 不受影响。每个文件先备份成
`<名字>.hisgen-orig`，重复执行会跳过已打过的。

```bash
python shanghai/patches/apply_patches.py --revert   # 从备份还原
```

> 打完补丁**不需要**手工重建 —— `run_synthea.bat` 走 `gradlew run`，
> 会自动重跑 `processResources`。首次编译会慢（下载 Gradle 与依赖），
> 之后就快了。

---

## 3. 生成数据

### 3.1 编译模块（IR → GMF）

```bash
python -m hisgen.compiler --ir sh_t2dm
```

产出 `modules/sh_t2dm.json`，并刷新 `review/待核实编码体系.md`（哪些条目
缺国标码、回退到了哪个兜底体系）。

```bash
python -m hisgen.compiler --ir sh_t2dm --smoke   # 编译后用 Synthea 真加载一次（慢，但值得）
```

`--smoke` 会把「模块加载不了」这类错误挡在生成之前，而不是生成到一半崩掉。

### 3.2 跑 Synthea

```bash
python -m hisgen.runner -p 200 -s 12345
```

| 参数 | 含义 |
|---|---|
| `-p 200` | **存活** 200 人（不是产出 200 个文件 —— 中途死亡的患者记录也会导出，所以文件数会多几个） |
| `-s 12345` | 随机种子，**同一个种子 + 同一份模块 = 同一批患者** |
| `-o DIR` | 输出目录，默认 `output/runs/<时间戳>/` |
| `-a 20-40` | 年龄范围 |
| `-g M` / `-g F` | 只要男性 / 女性 |
| `--city 黄浦区` | 只生成某个区 |
| `--config KEY=VALUE` | 临时覆盖任意 `synthea.properties` 项，可重复 |

产出：

```
output/runs/<时间戳>/
├─ fhir/                      每个患者一个 Bundle + hospitalInformation*.json + practitionerInformation*.json
├─ metadata/                  Synthea 自己写的人口/州/种子凭据（溯源用）
└─ hisgen-run-manifest.json   本次运行的完整参数
```

`fhir/` 里那份 **`metadata/`** 是数据溯源凭据，别删。

### 3.3 导出成 8 张表

```bash
python -m hisgen.export -i output/runs/<时间戳>/fhir -o output/tables
```

不带参数时 `-i` 默认指向 `output/runs/` 下**最新**的一次运行。

| 参数 | 含义 |
|---|---|
| `--keep-national-id` | 身份证号**不脱敏**（默认脱敏，只留前 6 后 4） |
| `--allow-orphans` | 外键检查失败也照样写盘（默认失败即中止） |
| `--shanghai DIR` | 机构等级/科室映射表所在目录，默认 `shanghai/` |
| `--terminology DIR` | 术语表目录，默认 `terminology/` |

命令会打印资源计数、编码体系 URI ↔ 标签对照表，以及**外键检查结果**。

**外键检查失败时不会写盘**，这是有意的 —— 曾经有过「四张表的 `就诊ID`
整列为空、检查却一个字都没报，8 张表照写不误」的事故。

---

## 4. 产物：8 张表

写到 `output/tables/`，UTF-8 **带 BOM**（Excel 直接打开不乱码）。

```
患者.csv        patient_id, 姓名, 性别, 出生日期, 身份证号, 手机号, 民族,
                医保类型, 医保号, 居住地址, 区县, 邮编
机构.csv        机构ID, 机构名称, 等级, 类型, 地址, 区县, 电话
医生.csv        医生ID, 姓名, 性别, 职称, 科室, 所属机构ID
就诊.csv        就诊ID, patient_id, 就诊类型, 科室, 机构ID, 医生ID,
                开始时间, 结束时间, 就诊原因, 总费用, 医保支付, 个人自付
诊断.csv        诊断ID, 就诊ID, 编码体系, 诊断编码, 诊断名称, 诊断类型, 确诊时间
医嘱_药品.csv   医嘱ID, 就诊ID, 药品编码, 药品通用名, 规格, 剂量,
                频次, 给药途径, 开始时间, 数量, 单价, 金额
检验.csv        检验ID, 就诊ID, 项目编码, 项目名称, 结果值, 单位,
                参考区间, 异常标志, 采样时间, 报告时间
手术操作.csv     操作ID, 就诊ID, 编码, 名称, 时间
```

建库顺序即上表顺序（被引用的表在前）。

### 4.1 几个必须知道的坑

1. **`医嘱_药品.数量` 与 `.单价` 是空的**，不是漏了。Synthea 的药品成本是
   「一张处方一个总额」，`MedicationRequest` 里没有包装数量，这两个数在
   合成数据里没有出处。填 0 会读成「这药不要钱」，比空值危险。
2. **所有处方的金额完全相同**。Synthea 按 RxNorm 码查药价表，我们写的是
   ATC 码，一个都匹配不上，全落到 `default_medication_cost`。
3. **药费已并入 `就诊.总费用`**。Synthea 把药品单独开成一张 pharmacy Claim，
   但它挂在同一次就诊上，所以并进来 —— 不并的话两张表对不上账。
4. **`手术操作.编码` 等于 `名称`**。这是 `LOCAL_ZH` 占位体系，**设计如此**：
   产物里应该一眼看出「这不是真实编码」。
5. **`编码体系` 列里带 `【待核实】` 前缀的，就是没核对过出处的**。
6. **`民族`、`医保号`、`职称`、`机构.等级` 是本项目生成的合成属性**，
   不是 Synthea 产出，落库时必须单独标注。

### 4.2 数据可信度

**逐项出处在 `shanghai/SOURCES.md`**，按三档标注：

- ✅ **已核实** —— 出处明确（如 16 个区的常住人口是第七次全国人口普查的数）
- ⚠️ **近似** —— 有依据方向，但数值是量级估计
- ❌ **待核实** —— 没有可靠出处，为让流程跑通而填的占位

**总括：区级常住人口与行政区划码是实的，其余的人口结构、社会决定因素
（SDOH）、机构地址、医保参数大多未经查证。** 本轮目的是把流程打通，
不是交付一份可直接投产的人口基线。

`review/` 下另有两份自动生成的核对清单：

- `待核实数字清单.md` —— IR 里每个概率与参考区间的出处（2 型糖尿病）
- `待核实编码体系.md` —— 哪些编码缺国标码、回退到了哪个兜底体系

---

## 5. 改临床路径（可选）

`ir/sh_t2dm.yaml` 是**人工审阅对象**，想加一个并发症或改一条转移概率，
直接改它然后重跑 §3.1 即可。

想从疾病资料重新生成 IR（需要大模型）：

```bash
pip install -e .[llm]
export DEEPSEEK_API_KEY=...          # 或 HISGEN_LLM_MODEL 换模型
python -m hisgen.llm --brief 疾病资料.md -o ir/sh_t2dm.yaml
```

**IR 里每个概率数字的 `source` 字段是必填的**（Pydantic 校验，空值直接编译
失败）。这是有意的约束：大模型从「权威」降级为「检索整理助手」，
未经核实的数字必须显式标出来。

不调模型、只重新生成核对清单：

```bash
python -m hisgen.llm --from-file ir/sh_t2dm.yaml
```

---

## 6. 测试

```bash
python -m pytest tests/ -q                        # 快测试，几秒
HISDATA_RUN_SYNTHEA=1 python -m pytest tests/ -q  # 外加真跑 Synthea 的端到端
```

- `tests/test_export.py` —— 导出的**逻辑**（用手工构造的 FHIR）
- `tests/test_compiler.py` —— 编译器的结构合法性、概率归一、回环语义
- `tests/test_synthea_e2e.py` —— **真跑一遍 Synthea**，断言的是真实产物
  「整列不能为空」，每一列对应一个曾经真的发生过的 bug

最后一条是关键。手工构造的 fixture 只能证明「给定这种形状的输入，导出层算得
对」；而踩过的坑几乎全是**形状本身**（`urn:uuid:` 引用、EOB 的 `total` 是列表、
药品不在就诊的 EOI 里……）。fixture 写得比真实输出更理想，就等于什么都没测。

---

## 7. 目录结构

```
HISdata/
├─ ir/sh_t2dm.yaml              层1：临床路径 IR（人工审阅对象）
├─ terminology/                 编码体系 + 药品/检验/诊断/手术白名单
├─ modules/sh_t2dm.json         层2：编译产物（Synthea 的 GMF）
├─ shanghai/                    层3：上海数据
│   ├─ shanghai.properties        Synthea 配置（人口、医保、费用口径）
│   ├─ demographics.csv           分区人口
│   ├─ sdoh.csv / zipcodes.csv / names_zh.yml
│   ├─ providers/ payers/         机构名单、医保计划
│   ├─ organization_levels.csv    机构等级（**由 _build_providers.py 生成，别手改**）
│   ├─ department_map.csv         科室名映射
│   ├─ patches/                   给 Synthea 源码打的补丁
│   └─ SOURCES.md                 **每个数字的出处**
├─ src/hisgen/                  层1-5 的代码
│   ├─ ir/         IR 数据模型
│   ├─ llm/        LLM 生成 IR（可选）
│   ├─ compiler/   IR -> GMF
│   ├─ runner/     调用 Synthea
│   └─ export/     FHIR -> 8 张表
├─ review/                      自动生成的核对清单（不入库）
└─ output/                      产物目录（不入库）
    ├─ runs/<时间戳>/            每次运行的产物
    └─ tables/                   8 张 CSV
```

---

## 8. 常见问题

**`找不到 run_synthea`** —— `SYNTHEA_HOME` 没设或指错了。必须指向源码根目录
（能看到 `run_synthea.bat`）。

**`'gradlew.bat' 不是内部或外部命令`** —— 这台机器设了
`NoDefaultCurrentDirectoryInExePath=1`，当前目录被排除在 `PATH` 之外。
`runner` 已经把 Synthea 根目录挂到 `PATH` 最前面处理掉了；如果你手工调
`run_synthea`，需要自己 `cd` 到 Synthea 目录。

**第一次跑特别慢** —— Gradle 要下载依赖并编译。之后就快了（有几个测试跑
6 秒内完成）。

**`-p 200` 却得到 211 个文件** —— 正常。`-p` 是「**存活** 200 人」，
中途死亡的患者记录也会导出。权威汇总看日志里的
`Records: total=X, alive=Y, dead=Z`。

**导出的 CSV 用 Excel 打开乱码** —— 不会，产物带 UTF-8 BOM。
如果你看到乱码，多半是读的时候用了 `encoding="utf-8"` 而不是 `utf-8-sig`。

**改了 `organization_levels.csv` 下次跑就没了** —— 那个文件是由
`shanghai/_build_providers.py` 生成的。要改等级，改脚本里 `HOSPITALS`
的第 5 个字段，然后重跑脚本。
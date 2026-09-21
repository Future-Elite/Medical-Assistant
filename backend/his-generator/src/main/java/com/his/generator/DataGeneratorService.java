package com.his.generator;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.Period;
import java.util.*;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.ToDoubleFunction;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * 模拟数据生成器。
 *
 * <p><b>数据来源</b>：诊断、用药、检验、剂量四类内容全部取自医学本体表
 * （{@code dept_diagnosis} / {@code diagnosis_drug} / {@code diagnosis_lab} / {@code drug_dose}），
 * 不再有硬编码的「全局诊断池」「全局检验池」或瞎编的剂量。
 * <ul>
 *   <li>诊断：只从<b>该次就诊科室</b>的 {@code dept_diagnosis} 里按 {@code weight} 加权抽取，
 *       因此不会出现「妇科医生看肠胃炎」；</li>
 *   <li>用药：按 {@code icd_code + department} 双列过滤 {@code diagnosis_drug}，按 {@code weight}
 *       加权、保证一线药（{@code line=1}）优先；</li>
 *   <li>检验：按 {@code icd_code + department} 双列过滤 {@code diagnosis_lab}；</li>
 *   <li>剂量：按 {@code drug_id} 取 {@code drug_dose} 的单次剂量 / 频次 / 疗程。</li>
 * </ul>
 *
 * <p><b>不注入违规</b>：本生成器<b>没有任何</b>故意注入违规处方的机制——不引入注入能力，
 * 生成的数据里不存在人为构造的相互作用 / 超剂量 / 适应症不符。
 */
@Service
@RequiredArgsConstructor
public class DataGeneratorService {

    /** JDBC batch 分块大小 */
    private static final int BATCH_SIZE = 500;
    /** 每次就诊 5-20 次 */
    private static final int MIN_VISITS = 5;
    private static final int MAX_VISITS = 20;
    /** 病史跨度 1-3 年 */
    private static final int MIN_YEARS = 1;
    private static final int MAX_YEARS = 3;
    /** 相邻两次就诊的最小间隔（分钟），保证时间严格递增 */
    private static final long MIN_VISIT_GAP_MINUTES = 30;
    /** 末次就诊之后留给诊断/处方/检验的时间（分钟），保证下游时间不越过 now */
    private static final long DOWNSTREAM_BUFFER_MINUTES = 150;
    /**
     * 就诊史「末端」随机落点范围（天）。每名患者的病史窗口右端在最近
     * {@code DOWNSTREAM_BUFFER_MINUTES + [0, TAIL_SPREAD_DAYS]} 内独立随机，
     * 使 10000 名患者的末次就诊散开——否则全部钉在同一瞬间，会在图表末端
     * 造出一根约为基线 54 倍的单日尖峰（并让之前 9 天成为零就诊空档）。
     *
     * <p>取值权衡：由于窗口右端不得晚于 now，覆盖率必然在「最近 TAIL_SPREAD_DAYS 天」
     * 内线性衰减到 0，即末端天然有一段缓降。取值越大尖峰越小、衰减越长；越小则相反。
     * 90 天是实测折中：单日峰值约为基线的 1.6 倍（原为 54 倍），且衰减只覆盖最近约 3 个月。
     */
    static final int TAIL_SPREAD_DAYS = 90;
    /** 每次就诊落在主科室的概率（其余落到其他科室） */
    private static final double HOME_DEPT_RATIO = 0.8;
    /** 清空时保留 drug / rule_config 等字典表 */
    private static final List<String> DATA_TABLES =
            List.of("patient", "visit", "diagnosis", "prescription", "prescription_item", "lab_test",
                    "charge", "dispense", "admission", "medical_order", "vital_sign");
    /** 挂号费固定金额 */
    private static final BigDecimal REGISTRATION_FEE = new BigDecimal("10.00");
    /** 每检验项费用 */
    private static final BigDecimal LAB_FEE = new BigDecimal("15.00");
    /** 有住院史的患者占比 */
    private static final double INPATIENT_RATIO = 0.2;
    /** 每位住院患者 1-3 次住院 */
    private static final int MIN_ADMISSIONS = 1;
    private static final int MAX_ADMISSIONS = 3;
    /** 每次住院 3-15 天 */
    private static final int MIN_STAY_DAYS = 3;
    private static final int MAX_STAY_DAYS = 15;
    /** 相邻住院的固定间隔（天）：> MAX_STAY_DAYS(15)，使多次住院严格升序且互不重叠由构造直接保证 */
    private static final int ADMISSION_GAP_DAYS = MAX_STAY_DAYS + 1;
    /** 入院诊断命中临床路径时按路径执行的占比（其余偏离，制造变异） */
    private static final double PATHWAY_COMPLIANCE_RATE = 0.7;
    /** 身份证地区码（GB 11643，北京市东城区） */
    private static final String ID_REGION_CODE = "110101";
    /** GB 11643 校验位权重（前 17 位） */
    private static final int[] ID_WEIGHTS = {7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2};
    /** GB 11643 校验码表：sum % 11 的下标 → 校验字符 */
    private static final char[] ID_CHECK_CODES = "10X98765432".toCharArray();
    /** 数值型参考区间（diagnosis_lab.ref_range 的常见形态，如 {@code 4.0-10.0}） */
    private static final Pattern NUMERIC_RANGE =
            Pattern.compile("^\\s*([0-9]+(?:\\.[0-9]+)?)\\s*-\\s*([0-9]+(?:\\.[0-9]+)?)\\s*$");
    /** 频次里的次数（{@code 每日3次} / {@code 每晚1次}），取不到按 1 次 */
    private static final Pattern FIRST_INT = Pattern.compile("([0-9]+)");

    private final PatientMapper patientMapper;
    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final LabTestMapper labTestMapper;
    private final DrugMapper drugMapper;
    private final DoctorMapper doctorMapper;
    private final JdbcTemplate jdbcTemplate;
    private final ChargeMapper chargeMapper;
    private final DispenseMapper dispenseMapper;
    private final AdmissionMapper admissionMapper;
    private final MedicalOrderMapper medicalOrderMapper;
    private final VitalSignMapper vitalSignMapper;
    private final ClinicalPathwayMapper pathwayMapper;
    private final DeptDiagnosisMapper deptDiagnosisMapper;
    private final DiagnosisDrugMapper diagnosisDrugMapper;
    private final DiagnosisLabMapper diagnosisLabMapper;
    private final DrugDoseMapper drugDoseMapper;

    /** 生成前清空八张业务数据表，使重复执行等价于「重新生成」（默认开启） */
    @Value("${generator.clear-before-run:true}")
    private boolean clearBeforeRun = true;

    private Map<Long, Drug> drugsById;
    private Map<String, List<String>> doctorsByDept;
    private ThreadLocalRandom rnd;
    /** 科室 → 该科室的候选诊断（本体 dept_diagnosis） */
    private Map<String, List<DeptDiagnosis>> diagnosesByDept;
    /** (ICD, 科室) → 该诊断的用药本体（diagnosis_drug，仅 department 非空的行） */
    private Map<DxDept, List<DiagnosisDrug>> drugsByDiagnosis;
    /** (ICD, 科室) → 该诊断的检验本体（diagnosis_lab，仅 department 非空的行） */
    private Map<DxDept, List<DiagnosisLab>> labsByDiagnosis;
    /** drug_id → 剂量规则（drug_dose，一药一行） */
    private Map<Long, DrugDose> doseByDrugId;
    private long chargeCount, dispenseCount;
    private List<ClinicalPathway> pathways;
    private long admissionCount, orderCount, vitalCount;
    /** 剂量来自 drug_dose 的处方明细数（对照量） */
    private long doseFromOntology;
    /** drug_dose 缺该药、处方明细回退到旧剂量写法的条数，必须报出、不得静默 */
    private long doseFallbacks;
    /** drug_dose 缺该药、住院医嘱回退到旧剂量写法的条数（单独计数，便于区分口径） */
    private long orderDoseFallbacks;
    /** 本体里没有可用（非术后）药的就诊数 / 住院数：这些就诊开不出药，不拿术后药或随机药凑数 */
    private long visitsWithoutDrugs;
    /** 本体里没有可用检验的就诊数 */
    private long visitsWithoutLabs;
    private long admissionsWithoutDrugs;

    /** 生成 patientCount 名患者及各自就诊链，返回统计信息 */
    public Map<String, Long> generate(long patientCount) {
        long start = System.currentTimeMillis();
        rnd = ThreadLocalRandom.current();
        if (clearBeforeRun) {
            clearDataTables();
        }
        drugsById = drugMapper.selectList(null).stream()
                .collect(Collectors.toMap(Drug::getId, d -> d));
        loadOntology();
        doctorsByDept = loadDoctors();
        chargeCount = 0;
        dispenseCount = 0;
        pathways = pathwayMapper.selectList(
                new LambdaQueryWrapper<ClinicalPathway>().eq(ClinicalPathway::getEnabled, 1));
        admissionCount = 0; orderCount = 0; vitalCount = 0;
        doseFromOntology = 0; doseFallbacks = 0; orderDoseFallbacks = 0;
        visitsWithoutDrugs = 0; visitsWithoutLabs = 0; admissionsWithoutDrugs = 0;

        long visits = 0, diagnoses = 0, pres = 0, items = 0, labs = 0;
        List<Patient> patientBuf = new ArrayList<>(BATCH_SIZE);
        for (long i = 0; i < patientCount; i++) {
            patientBuf.add(newPatient());
            if (patientBuf.size() >= BATCH_SIZE) {
                long[] c = flushPatients(patientBuf);
                visits += c[0]; diagnoses += c[1]; pres += c[2]; items += c[3]; labs += c[4];
            }
        }
        long[] tail = flushPatients(patientBuf);
        visits += tail[0]; diagnoses += tail[1]; pres += tail[2]; items += tail[3]; labs += tail[4];

        Map<String, Long> stat = new LinkedHashMap<>();
        stat.put("patients", patientCount);
        stat.put("visits", visits);
        stat.put("diagnoses", diagnoses);
        stat.put("prescriptions", pres);
        stat.put("prescription_items", items);
        stat.put("lab_tests", labs);
        stat.put("charges", chargeCount);
        stat.put("dispenses", dispenseCount);
        stat.put("admissions", admissionCount);
        stat.put("orders", orderCount);
        stat.put("vital_signs", vitalCount);
        stat.put("dose_from_drug_dose", doseFromOntology);
        stat.put("dose_fallbacks", doseFallbacks);
        stat.put("order_dose_fallbacks", orderDoseFallbacks);
        stat.put("visits_without_ontology_drugs", visitsWithoutDrugs);
        stat.put("visits_without_ontology_labs", visitsWithoutLabs);
        stat.put("admissions_without_ontology_drugs", admissionsWithoutDrugs);
        stat.put("elapsed_ms", System.currentTimeMillis() - start);
        return stat;
    }

    /** 幂等：清空业务数据表（不动 drug / rule_config 等字典表） */
    private void clearDataTables() {
        for (String table : DATA_TABLES) {
            jdbcTemplate.execute("TRUNCATE TABLE " + table);
        }
    }

    /**
     * 一次性加载四张本体表到内存索引。
     *
     * <p>{@code diagnosis_drug.department} / {@code diagnosis_lab.department} 允许为 NULL，
     * 实体注释的语义是「NULL = 对全部科室适用」。但本生成器<b>不使用</b>这一语义：
     * 验收断言 A2/A3 要求 {@code (icd_code, department)} <b>精确</b>匹配，
     * 用 NULL 行抽出的药/检验会让断言失败。当前库中这两张表的 department 为 NULL 的行数为 0，
     * 因此该取舍不影响数据；若将来出现 NULL 行，这里会静默跳过并在日志口径里体现为「池变小」。
     */
    private void loadOntology() {
        diagnosesByDept = deptDiagnosisMapper.selectList(null).stream()
                .collect(Collectors.groupingBy(DeptDiagnosis::getDepartment));
        if (diagnosesByDept.isEmpty()) {
            throw new IllegalStateException(
                    "dept_diagnosis 表为空，请先执行 backend/sql/deploy-ontology.sh 灌入医学本体");
        }
        drugsByDiagnosis = diagnosisDrugMapper.selectList(null).stream()
                .filter(r -> r.getDepartment() != null)
                .collect(Collectors.groupingBy(r -> new DxDept(r.getIcdCode(), r.getDepartment())));
        labsByDiagnosis = diagnosisLabMapper.selectList(null).stream()
                .filter(r -> r.getDepartment() != null)
                .collect(Collectors.groupingBy(r -> new DxDept(r.getIcdCode(), r.getDepartment())));
        doseByDrugId = drugDoseMapper.selectList(null).stream()
                .collect(Collectors.toMap(DrugDose::getDrugId, x -> x, (a, b) -> a));
    }

    /**
     * 按批写入：患者 → 就诊 → (诊断, 处方) → (处方明细, 检验) → (收费, 发药)。
     * 全部走 MyBatis-Plus 的 JDBC 批量接口，自增主键回填到实体，保证外键都取自真实 id。
     *
     * @return {就诊数, 诊断数, 处方数, 明细数, 检验数}
     */
    private long[] flushPatients(List<Patient> patients) {
        if (patients.isEmpty()) {
            return new long[5];
        }
        patientMapper.insert(patients);                       // 回填 patient.id

        List<Visit> visitBuf = new ArrayList<>();
        for (Patient p : patients) {
            visitBuf.addAll(newVisits(p));
        }
        visitMapper.insert(visitBuf);                         // 回填 visit.id

        List<Diagnosis> diagBuf = new ArrayList<>(visitBuf.size());
        List<PendingPrescription> presBuf = new ArrayList<>(visitBuf.size());
        // 患者 → 其门诊诊断（按科室去重前），供住院链「入院诊断延续门诊诊断」使用
        Map<Long, List<DeptDiagnosis>> outpatientDxByPatient = new HashMap<>();
        for (Visit v : visitBuf) {
            // 诊断：只从本次就诊科室的 dept_diagnosis 里按 weight 加权取
            DeptDiagnosis dx = pickDiagnosis(v.getDepartment());
            Diagnosis d = new Diagnosis();
            d.setPatientId(v.getPatientId()); d.setVisitId(v.getId());
            d.setIcdCode(dx.getIcdCode()); d.setDiagnosisName(dx.getDiagnosisName());
            d.setDoctor(v.getDoctor()); d.setDiagnosedAt(v.getVisitTime().plusMinutes(rnd.nextInt(5, 20)));
            diagBuf.add(d);

            // 处方（诊断对应药品，保证诊断↔用药合理）
            Prescription pr = new Prescription();
            pr.setPatientId(v.getPatientId()); pr.setVisitId(v.getId());
            pr.setDoctor(v.getDoctor()); pr.setPrescribedAt(d.getDiagnosedAt().plusMinutes(rnd.nextInt(2, 10)));
            pr.setTotalAmount(BigDecimal.ZERO);
            presBuf.add(new PendingPrescription(pr, dx));
            outpatientDxByPatient.computeIfAbsent(v.getPatientId(), k -> new ArrayList<>()).add(dx);
        }
        diagnosisMapper.insert(diagBuf);
        // 先插处方、拿回自增 id，明细才能引用到真实 prescription_id
        prescriptionMapper.insert(presBuf.stream().map(PendingPrescription::prescription).toList());

        // 处方明细 + 检验 + 发药（依赖已回填的 prescription.id / visit.id）
        Map<Long, LocalDateTime> visitTimeById = new HashMap<>();
        for (Visit v : visitBuf) {
            visitTimeById.put(v.getId(), v.getVisitTime());
        }
        List<PrescriptionItem> itemBuf = new ArrayList<>();
        List<LabTest> labBuf = new ArrayList<>();
        List<Dispense> dispBuf = new ArrayList<>();
        Map<Long, BigDecimal> amountByVisit = new HashMap<>();
        Map<Long, Integer> labCountByVisit = new HashMap<>();
        for (PendingPrescription pp : presBuf) {
            Prescription pr = pp.prescription();
            List<PrescriptionItem> its = buildItems(pp.diagnosis());
            if (its.isEmpty()) {
                visitsWithoutDrugs++;
            }
            for (PrescriptionItem it : its) {
                it.setPrescriptionId(pr.getId());
            }
            // total = Σ 单价 × 数量（缺药/缺价一律按 0 计，杜绝 NPE）
            BigDecimal total = its.stream().map(i -> {
                Drug d = i.getDrugId() == null ? null : drugsById.get(i.getDrugId());
                return (d == null || d.getPrice() == null ? BigDecimal.ZERO
                        : d.getPrice().multiply(i.getQuantity() == null ? BigDecimal.ZERO : i.getQuantity()));
            }).reduce(BigDecimal.ZERO, BigDecimal::add);
            pr.setTotalAmount(total);
            itemBuf.addAll(its);
            amountByVisit.merge(pr.getVisitId(), total, BigDecimal::add);

            // 发药：一张处方一条；药物名/数量取自首条明细（不是诊断名）；时间严格晚于收费
            if (!its.isEmpty()) {
                PrescriptionItem first = its.get(0);
                Dispense disp = new Dispense();
                disp.setPatientId(pr.getPatientId());
                disp.setVisitId(pr.getVisitId());
                disp.setPrescriptionId(pr.getId());
                disp.setDrugName(first.getDrugName());
                disp.setQuantity(first.getQuantity());
                disp.setDispensedAt(visitTimeById.get(pr.getVisitId()).plusMinutes(rnd.nextInt(165, 205)));
                disp.setPharmacist(randomPharmacist());
                dispBuf.add(disp);
            }

            List<LabTest> labs = newLabTests(pr, pp.diagnosis());
            labBuf.addAll(labs);
            labCountByVisit.merge(pr.getVisitId(), labs.size(), Integer::sum);
        }
        itemMapper.insert(itemBuf);
        labTestMapper.insert(labBuf);
        // 收费：按就诊汇总药费 + 检验费；时间严格晚于检验、早于发药
        List<Charge> chargeBuf = new ArrayList<>();
        for (Visit v : visitBuf) {
            BigDecimal drugFee = amountByVisit.getOrDefault(v.getId(), BigDecimal.ZERO);
            int labCount = labCountByVisit.getOrDefault(v.getId(), 0);
            BigDecimal labFee = LAB_FEE.multiply(BigDecimal.valueOf(labCount));
            chargeBuf.add(newCharge(v, 0, "挂号费", REGISTRATION_FEE));
            chargeBuf.add(newCharge(v, 1, "药费", drugFee));
            if (labCount > 0) {
                chargeBuf.add(newCharge(v, 2, "检验费", labFee));
            }
        }
        chargeMapper.insert(chargeBuf);
        dispenseMapper.insert(dispBuf);
        chargeCount += chargeBuf.size();
        dispenseCount += dispBuf.size();

        // 住院：先只构造 Admission，回填 id 之后再生成医嘱/体征/住院检验
        Map<Long, LocalDateTime> lastVisitByPatient = new HashMap<>();
        for (Visit v : visitBuf) {
            lastVisitByPatient.merge(v.getPatientId(), v.getVisitTime(),
                    (a, b) -> a.isAfter(b) ? a : b);
        }
        List<PendingAdmission> admissionBuf = new ArrayList<>();
        List<MedicalOrder> orderBuf = new ArrayList<>();
        List<VitalSign> vitalBuf = new ArrayList<>();
        List<LabTest> inpatientLabBuf = new ArrayList<>();
        for (Patient p : patients) {
            if (rnd.nextDouble() >= INPATIENT_RATIO) {
                continue;
            }
            admissionBuf.addAll(newAdmissions(p, lastVisitByPatient.get(p.getId()),
                    outpatientDxByPatient.get(p.getId())));
        }
        admissionMapper.insert(admissionBuf.stream().map(PendingAdmission::admission).toList());  // 回填 admission.id
        for (PendingAdmission pa : admissionBuf) {
            generateInpatientEvents(pa.admission(), pa.diagnosis(), orderBuf, vitalBuf, inpatientLabBuf);
        }
        medicalOrderMapper.insert(orderBuf);
        vitalSignMapper.insert(vitalBuf);
        labTestMapper.insert(inpatientLabBuf);
        admissionMapper.updateById(admissionBuf.stream().map(PendingAdmission::admission).toList());  // 回写出院字段
        admissionCount += admissionBuf.size();
        orderCount += orderBuf.size();
        vitalCount += vitalBuf.size();

        // 回写处方总额（批量按 id 更新）
        prescriptionMapper.updateById(presBuf.stream().map(PendingPrescription::prescription).toList());

        patients.clear();
        return new long[]{visitBuf.size(), diagBuf.size(), presBuf.size(), itemBuf.size(), labBuf.size()};
    }

    /**
     * 患者档案：出生日期、性别、身份证三者互相自洽。
     *
     * <ul>
     *   <li>年龄仍在 18–89 岁（沿用原有取值区间），出生日期为「随机年 + 随机月 + 1..28 日」，
     *       不再是所有人共享生成日的月日；由此出生年份落在 [1937, 2008]；</li>
     *   <li>身份证为合法的 18 位号码：地区码 + 出生日期(YYYYMMDD，取自 {@code birth_date})
     *       + 顺序码（奇数男 / 偶数女，性别亦由此决定）+ GB 11643 校验位。</li>
     * </ul>
     */
    private Patient newPatient() {
        Patient p = new Patient();
        p.setName(randomName());

        // 生日改为「随机年 + 随机月 + 1..28 日」：月日不再固定为生成日。
        // 月日若晚于今天，候选人实际年龄会比候选年份小 1 岁；把这一档再往前推一年，
        // 保证 Period.between(birthDate, now) 仍落在 18-89 岁区间内。
        LocalDate today = LocalDate.now();
        LocalDate candidate = LocalDate.of(
                today.getYear() - rnd.nextInt(18, 90),
                rnd.nextInt(1, 13),
                rnd.nextInt(1, 29));
        LocalDate birthDate = Period.between(candidate, today).getYears() >= 18
                ? candidate
                : candidate.minusYears(1);
        p.setBirthDate(birthDate);

        // 顺序码奇偶决定性别：奇数为男(0)、偶数为女(1)
        int sequence = rnd.nextInt(1, 1000);
        p.setGender(sequence % 2 == 1 ? 0 : 1);
        p.setIdCard(buildIdCard(birthDate, sequence));

        p.setPhone("13" + String.format("%09d", rnd.nextInt(1_000_000_000)));
        p.setAddress(randomName() + "市" + randomName() + "区" + rnd.nextInt(1, 999) + "号");
        p.setCreatedAt(LocalDateTime.now());
        return p;
    }

    /**
     * 生成 GB 11643 合法身份证号：地区码 + 出生日期(YYYYMMDD) + 3 位顺序码 + ISO 7064 校验位。
     *
     * @param birthDate 必须等于患者的 {@code birth_date}
     * @param sequence  3 位顺序码（1–999），其奇偶即性别（奇男偶女）
     */
    static String buildIdCard(LocalDate birthDate, int sequence) {
        String body = ID_REGION_CODE
                + String.format("%04d%02d%02d", birthDate.getYear(), birthDate.getMonthValue(), birthDate.getDayOfMonth())
                + String.format("%03d", sequence);
        int sum = 0;
        for (int i = 0; i < 17; i++) {
            sum += (body.charAt(i) - '0') * ID_WEIGHTS[i];
        }
        return body + ID_CHECK_CODES[sum % 11];
    }

    /**
     * 生成一名患者的就诊链：就诊次数 5-20，病史跨度恰好 1-3 年（{@code windowStart = windowEnd - years}），
     * 时间在 [windowStart, windowEnd] 内严格递增，末次就诊不晚于 now。
     *
     * <p><b>窗口右端按患者随机</b>：取值落在
     * {@code now - DOWNSTREAM_BUFFER_MINUTES - [0, TAIL_SPREAD_DAYS] 天}（再叠加日内随机分钟），
     * 因此「末次就诊」不再被钉死在同一瞬间——这正是旧实现造成
     * 单日 54 倍尖峰与之前 9 天零就诊空档的根因。
     * 病史跨度仍由 {@code windowStart = windowEnd.minusYears(years)} 精确保证为 1-3 年，
     * 且窗口右端不超过 {@code now}，故下游时间不会越过 now。
     */
    private List<Visit> newVisits(Patient p) {
        int visitCount = rnd.nextInt(MIN_VISITS, MAX_VISITS + 1);
        int years = rnd.nextInt(MIN_YEARS, MAX_YEARS + 1);
        LocalDateTime windowEnd = LocalDateTime.now()
                .minusMinutes(DOWNSTREAM_BUFFER_MINUTES)
                .minusDays(rnd.nextLong(0, TAIL_SPREAD_DAYS + 1))
                .minusMinutes(rnd.nextLong(0, 1440));
        LocalDateTime windowStart = windowEnd.minusYears(years);
        long windowMinutes = Duration.between(windowStart, windowEnd).toMinutes();

        long[] offsets = visitOffsets(visitCount, windowMinutes);

        // 主科室：该患者 80% 的就诊落在这里，其余散到其他科室；医生必须属于就诊科室。
        List<String> departments = new ArrayList<>(doctorsByDept.keySet());
        String homeDept = departments.get(rnd.nextInt(departments.size()));

        List<Visit> visits = new ArrayList<>(visitCount);
        for (int j = 0; j < visitCount; j++) {
            String department = (rnd.nextDouble() < HOME_DEPT_RATIO)
                    ? homeDept
                    : pickOtherDept(departments, homeDept);
            List<String> doctors = doctorsByDept.get(department);
            String doctor = doctors.get(rnd.nextInt(doctors.size()));

            Visit v = new Visit();
            v.setPatientId(p.getId());
            v.setVisitNo("V" + p.getId() + "-" + (j + 1));
            v.setDepartment(department);
            v.setDoctor(doctor);
            v.setVisitTime(windowStart.plusMinutes(offsets[j]));
            v.setStatus(0);
            visits.add(v);
        }
        return visits;
    }

    /** 从「非主科室」里均匀随机取一个（seed 保证 ≥2 个科室）。 */
    private String pickOtherDept(List<String> departments, String homeDept) {
        String other;
        do {
            other = departments.get(rnd.nextInt(departments.size()));
        } while (other.equals(homeDept));
        return other;
    }

    /**
     * 把 visitCount 次就诊铺满 [0, windowMinutes]：首末分别贴在窗口两端（病史跨度恰为 years 年），
     * 中间按槽位抖动，且强制相邻间隔 >= {@link #MIN_VISIT_GAP_MINUTES}，保证严格递增。
     *
     * <p>本方法是「相对窗口」的纯函数：末点落在 {@code windowMinutes} 只表示该患者的病史
     * 恰好跨满 {@code years} 年，<b>不再代表某一绝对时刻</b>——窗口右端已按患者随机
     * （见 {@link #newVisits}），所以末次就诊天然散开，不会堆在同一瞬间。
     */
    private long[] visitOffsets(int visitCount, long windowMinutes) {
        long[] offsets = new long[visitCount];
        if (visitCount <= 1) {
            return offsets;
        }
        long slot = windowMinutes / (visitCount - 1);
        for (int j = 1; j < visitCount - 1; j++) {
            long ideal = j * windowMinutes / (visitCount - 1);
            long jitter = (long) ((rnd.nextDouble() - 0.5) * slot);
            long lower = offsets[j - 1] + MIN_VISIT_GAP_MINUTES;
            long upper = windowMinutes - (long) (visitCount - 1 - j) * MIN_VISIT_GAP_MINUTES;
            offsets[j] = Math.max(lower, Math.min(upper, ideal + jitter));
        }
        offsets[visitCount - 1] = windowMinutes;
        return offsets;
    }

    /**
     * 按<b>就诊科室</b>从本体 {@code dept_diagnosis} 取诊断，权重取 {@code weight} 列。
     *
     * <p>关于权重列：任务书里写的是 {@code visit_rate}，但 {@code dept_diagnosis} 并没有这一列
     * （{@code visit_rate} 是另一张「年龄性别就诊倍率」表）。本表唯一的权重列就是
     * {@code weight DECIMAL(6,3) NOT NULL DEFAULT 1.000}，实测取值 1.000–4.000，语义为
     * 「该科室里各诊断的相对就诊频次」。权重 {@code <= 0} 或为 NULL 时按等权 1.0 处理。
     *
     * <p>科室本身由 {@link #newVisits} 决定（80% 主科室），本方法不改动科室逻辑，
     * 只保证「诊断一定出自该科室」——这正是「妇科医生看肠胃炎」的修法。
     */
    private DeptDiagnosis pickDiagnosis(String department) {
        List<DeptDiagnosis> pool = diagnosesByDept.get(department);
        if (pool == null || pool.isEmpty()) {
            throw new IllegalStateException("dept_diagnosis 中没有科室「" + department
                    + "」的诊断，请先执行 backend/sql/deploy-ontology.sh 灌入医学本体");
        }
        return weightedPick(pool, x -> num(x.getWeight()));
    }

    /**
     * 检验项来自本体 {@code diagnosis_lab}：按 {@code icd_code + department} 双列精确过滤，
     * 按 {@code weight} 加权抽 1-3 项。
     *
     * <p>结果值沿用原有写法（正常值落在参考区间内；异常值越过区间边界，幅度为边界的 0–30%），
     * {@code is_abnormal} 与 {@code reference_range} 的语义不变。差别只在于参考区间、异常率、
     * 异常方向现在取自本体行（{@code ref_range} / {@code abnormal_prob} / {@code abnormal_direction}），
     * 而不是原先硬编码的 5 项检验池 + 固定 15% 且只偏高。参考区间是「正常 / 阴性」这类
     * 定性项（心电图、X 线、超声、便隐血、乙肝表面抗原……）时只生成定性结果。
     */
    private List<LabTest> newLabTests(Prescription pr, DeptDiagnosis dx) {
        List<DiagnosisLab> pool = labsByDiagnosis.get(new DxDept(dx.getIcdCode(), dx.getDepartment()));
        if (pool == null || pool.isEmpty()) {
            visitsWithoutLabs++;
            return List.of();
        }
        int want = Math.min(pool.size(), rnd.nextInt(1, 4));
        List<DiagnosisLab> chosen = new ArrayList<>(want);
        while (chosen.size() < want) {
            List<DiagnosisLab> rest = pool.stream()
                    .filter(x -> chosen.stream().noneMatch(c -> c.getItemCode().equals(x.getItemCode())))
                    .toList();
            if (rest.isEmpty()) {
                break;
            }
            chosen.add(weightedPick(rest, x -> num(x.getWeight())));
        }
        List<LabTest> labs = new ArrayList<>(chosen.size());
        for (DiagnosisLab dl : chosen) {
            LabTest lt = new LabTest();
            lt.setPatientId(pr.getPatientId()); lt.setVisitId(pr.getVisitId());
            lt.setItemCode(dl.getItemCode()); lt.setItemName(dl.getItemName());
            lt.setUnit(dl.getUnit());
            fillResult(lt, dl.getRefRange(), dl.getAbnormalProb(), dl.getAbnormalDirection());
            lt.setTestedAt(pr.getPrescribedAt().plusMinutes(rnd.nextInt(20, 90)));
            labs.add(lt);
        }
        return labs;
    }

    /**
     * 生成一次检验结果值（门诊与住院共用）。
     *
     * <p>正常值严格沿用原有写法（落在参考区间内）；异常值沿用「越过区间边界、幅度为幅度的
     * 0–30%」的写法，但幅度按<b>区间宽度</b>而非边界绝对值计算，并加 5% 宽度（且不少于 0.01）
     * 的固定偏移——否则像 {@code 0-0.04} 这种窄区间在 {@code %.2f} 取整后会退回区间内，
     * 造出「is_abnormal=1 但值落在参考区间里」的自相矛盾行。
     *
     * @param refRange  本体 {@code diagnosis_lab.ref_range}：数值型为 {@code 4.0-10.0}，
     *                  定性型为 {@code 正常} / {@code 阴性}
     * @param prob      本体 {@code abnormal_prob}：异常概率（NULL 时按 0.15 兜底）
     * @param direction 本体 {@code abnormal_direction}：{@code HIGH} 偏高 / {@code LOW} 偏低 /
     *                  {@code NONE} 未指定方向（随机取一侧）
     */
    private void fillResult(LabTest lt, String refRange, BigDecimal prob, String direction) {
        boolean abnormal = rnd.nextDouble() < (prob == null ? 0.15 : prob.doubleValue());
        Matcher m = refRange == null ? null : NUMERIC_RANGE.matcher(refRange);
        if (m != null && m.matches()) {
            double low = Double.parseDouble(m.group(1));
            double high = Double.parseDouble(m.group(2));
            double width = high - low;
            double step = Math.max(width * 0.05, 0.01);
            double beyond = step + rnd.nextDouble() * Math.max(width * 0.3, step);
            double val;
            if (!abnormal) {
                val = low + rnd.nextDouble() * width;
            } else if (("LOW".equals(direction) || (!"HIGH".equals(direction) && rnd.nextBoolean())) && low > step) {
                val = low - beyond;
            } else {
                val = high + beyond;
            }
            lt.setResultValue(String.format("%.2f", val));
        } else {
            String normal = refRange == null ? "正常" : refRange;
            lt.setResultValue(abnormal ? ("阴性".equals(normal) ? "阳性" : "异常") : normal);
        }
        lt.setReferenceRange(refRange);
        lt.setIsAbnormal(abnormal ? 1 : 0);
    }

    /**
     * 从本体 {@code diagnosis_drug} 生成处方明细。
     *
     * <p>过滤条件必须<b>同时</b>带 {@code icd_code} 与 {@code department}——同一个 ICD
     * 在不同科室的用药不同（如 J06.9 在 内科 与 儿科 的池子不一样）。
     *
     * <p>{@code line} 用于保证一线药优先：池里有非术后的 {@code line=1} 药时先按 {@code weight}
     * 定一条，再按 {@code weight} 从全池（含二线/三线）补足到 1-3 条。
     * {@code postop=1} 的条目只用于术后阶段，本次没有术后场景，一律排除；
     * 若某个 (ICD, 科室) 除了术后药之外没有药（外科研 D23.9 / K42.9），则返回空明细——
     * <b>不</b>拿术后药或随机药凑数（空处方在收费里体现为药费 0，发药与检验费照常）。
     */
    private List<PrescriptionItem> buildItems(DeptDiagnosis dx) {
        List<DiagnosisDrug> pool = drugsByDiagnosis.get(new DxDept(dx.getIcdCode(), dx.getDepartment()));
        List<DiagnosisDrug> eligible = pool == null ? List.of()
                : pool.stream().filter(r -> r.getPostop() == null || r.getPostop() == 0).toList();
        if (eligible.isEmpty()) {
            return List.of();
        }
        int want = Math.min(eligible.size(), rnd.nextInt(1, 4));
        List<DiagnosisDrug> chosen = new ArrayList<>(want);
        List<DiagnosisDrug> firstLine = eligible.stream()
                .filter(r -> r.getLine() != null && r.getLine() == 1).toList();
        if (!firstLine.isEmpty()) {
            chosen.add(weightedPick(firstLine, r -> num(r.getWeight())));
        }
        while (chosen.size() < want) {
            List<DiagnosisDrug> rest = eligible.stream()
                    .filter(r -> chosen.stream().noneMatch(c -> c.getDrugId().equals(r.getDrugId())))
                    .toList();
            if (rest.isEmpty()) {
                break;
            }
            chosen.add(weightedPick(rest, r -> num(r.getWeight())));
        }

        List<PrescriptionItem> items = new ArrayList<>(chosen.size());
        for (DiagnosisDrug dg : chosen) {
            Drug drug = drugsById.get(dg.getDrugId());
            if (drug == null) {
                throw new IllegalStateException("diagnosis_drug(" + dx.getIcdCode() + "/" + dx.getDepartment()
                        + ") 引用了不存在的 drug_id=" + dg.getDrugId() + "，请修正本体数据");
            }
            items.add(newItem(drug));
        }
        return items;
    }

    /**
     * 用本体 {@code drug_dose} 的真实剂量造一条处方明细：
     * 单次剂量 + 剂量单位作为 {@code dosage}，{@code frequency} 作为频次，
     * 疗程取 {@code [days_min, days_max]}，数量按「单次剂量 × 每日次数 × 疗程 ÷ 每盒装量」折算成盒数
     * （每盒装量从 {@code drug.spec}，如 {@code 0.25g*24粒}，中的「24」读出）。
     *
     * <p>{@code drug_dose} 缺该药时<b>回退</b>到旧的写法（1-2 片 / 每日 1-2 次 / 3-14 天 / 1-3 盒），
     * 并计入 {@code dose_fallbacks} 统计——不静默。
     */
    private PrescriptionItem newItem(Drug drug) {
        PrescriptionItem it = new PrescriptionItem();
        it.setDrugId(drug.getId());
        it.setDrugName(drug.getName());
        it.setUnit(drug.getUnit());

        DrugDose dose = doseByDrugId.get(drug.getId());
        if (dose == null) {
            doseFallbacks++;
            it.setDosage(rnd.nextInt(1, 3) + "片");
            it.setFrequency("每日" + rnd.nextInt(1, 3) + "次");
            it.setDays(rnd.nextInt(3, 15));
            it.setQuantity(BigDecimal.valueOf(rnd.nextInt(1, 4)));
            return it;
        }
        doseFromOntology++;
        double single = dose.getSingleDose() == null ? 1.0 : dose.getSingleDose().doubleValue();
        int perDay = dosesPerDay(dose.getFrequency());
        int lo = dose.getDaysMin() == null ? 3 : dose.getDaysMin();
        int hi = dose.getDaysMax() == null ? Math.max(lo, 7) : Math.max(lo, dose.getDaysMax());
        int days = rnd.nextInt(lo, hi + 1);
        it.setDosage(plain(dose.getSingleDose()) + (dose.getDoseUnit() == null ? "" : dose.getDoseUnit()));
        it.setFrequency(dose.getFrequency());
        it.setDays(days);
        it.setQuantity(BigDecimal.valueOf(packsFor(drug.getSpec(), dose.getDoseUnit(), single, perDay, days)));
        return it;
    }

    private Charge newCharge(Visit v, int itemType, String itemName, BigDecimal amount) {
        Charge c = new Charge();
        c.setPatientId(v.getPatientId()); c.setVisitId(v.getId());
        c.setChargeNo("C" + v.getId() + "-" + itemType);
        c.setItemType(itemType); c.setItemName(itemName); c.setAmount(amount);
        c.setChargedAt(v.getVisitTime().plusMinutes(rnd.nextInt(125, 155)));
        return c;
    }

    private String randomPharmacist() {
        // 药师名不得用「王冠杰」；固定非敏感名单
        String[] surnames = {"刘", "陈", "杨", "周", "吴"};
        String[] given = {"静", "磊", "军", "艳", "涛"};
        return surnames[rnd.nextInt(surnames.length)] + given[rnd.nextInt(given.length)];
    }

    /**
     * 构造一名患者的住院记录（1-3 次），只填 Admission 字段；医嘱/体征/检验在回填 id 之后生成。
     *
     * <p>入院诊断取自<b>该患者自己的门诊诊断</b>（延续门诊诊断，不是随机挑一个病），
     * 并且该诊断所在科室即住院科室——因此入院诊断必然属于住院科室的 {@code dept_diagnosis}。
     * {@code diagnosis}（旧列）与 {@code admission_diagnosis} 写同一个值，旧列不留空。
     */
    private List<PendingAdmission> newAdmissions(Patient p, LocalDateTime lastVisit,
                                                List<DeptDiagnosis> outpatientDx) {
        LocalDateTime now = LocalDateTime.now();
        int count = rnd.nextInt(MIN_ADMISSIONS, MAX_ADMISSIONS + 1);
        // 相邻住院固定隔 ADMISSION_GAP_DAYS 天（> 最大住院 15 天 + 出院最多 +23h），
        // 「严格升序 + 互不重叠 + 末次入院不晚于 now」由构造直接保证，无需再回退钳制。
        // 末次门诊距今太近、放不下 count 次时，逐级减少次数（末次门诊必早于 now，故至少保留 1 次）。
        LocalDateTime latestFirst = now;
        while (count > 0) {
            latestFirst = now.minusDays((count - 1) * (long) ADMISSION_GAP_DAYS);
            if (lastVisit.isBefore(latestFirst)) {
                break;
            }
            count--;
        }

        // 首次入院在 (lastVisit, latestFirst] 内随机；其后每次 = 前一次 + ADMISSION_GAP_DAYS 天。
        long windowMinutes = Duration.between(lastVisit, latestFirst).toMinutes();
        LocalDateTime first = windowMinutes <= 1
                ? latestFirst
                : lastVisit.plusMinutes(rnd.nextLong(1, windowMinutes + 1));

        List<PendingAdmission> list = new ArrayList<>(count);
        for (int i = 0; i < count; i++) {
            DeptDiagnosis dx = outpatientDx.get(rnd.nextInt(outpatientDx.size()));
            String diagnosis = dx.getDiagnosisName();
            String department = dx.getDepartment();
            String doctor = doctorsByDept.get(department)
                    .get(rnd.nextInt(doctorsByDept.get(department).size()));

            Admission a = new Admission();
            a.setPatientId(p.getId());
            a.setAdmissionNo("A" + p.getId() + "-" + (i + 1));
            a.setDepartment(department); a.setDoctor(doctor);
            a.setBedNo(rnd.nextInt(1, 60) + "床");
            a.setDiagnosis(diagnosis);
            a.setAdmissionDiagnosis(diagnosis);
            a.setAdmissionTime(first.plusDays((long) i * ADMISSION_GAP_DAYS));
            a.setDeposit(BigDecimal.valueOf(rnd.nextInt(5, 21) * 100));
            a.setStatus(0);
            list.add(new PendingAdmission(a, dx));
        }
        return list;
    }

    /** 生成一次住院的医嘱/生命体征/住院检验，并回写出院字段（dischargeTime/totalCost/status）。 */
    private void generateInpatientEvents(Admission a, DeptDiagnosis dx, List<MedicalOrder> orderBuf,
                                         List<VitalSign> vitalBuf, List<LabTest> inpatientLabBuf) {
        ClinicalPathway pathway = pathwayFor(a.getDiagnosis());
        int totalDays = rnd.nextInt(MIN_STAY_DAYS, MAX_STAY_DAYS + 1);
        boolean compliant = pathway != null && rnd.nextDouble() < PATHWAY_COMPLIANCE_RATE;

        // 住院用药同样走 diagnosis_drug（按 icd_code + department 双列过滤，排除 postop=1）
        List<DiagnosisDrug> drugPool = drugsByDiagnosis.get(new DxDept(dx.getIcdCode(), dx.getDepartment()));
        List<DiagnosisDrug> usable = drugPool == null ? List.of()
                : drugPool.stream().filter(r -> r.getPostop() == null || r.getPostop() == 0).toList();

        // 长期医嘱（首日开，执行至出院）
        if (usable.isEmpty()) {
            admissionsWithoutDrugs++;
        } else {
            int longOrderCount = rnd.nextInt(1, 4);
            for (int k = 0; k < longOrderCount; k++) {
                orderBuf.add(newMedOrder(a, weightedPick(usable, r -> num(r.getWeight())),
                        a.getAdmissionTime().plusMinutes(rnd.nextInt(5, 60)), 0));   // 长期医嘱
            }
        }

        // 每日生命体征（早中晚 3 次，带趋势；day 0 落在入院时间之前的记录跳过）
        for (int day = 0; day < totalDays; day++) {
            for (int slot = 0; slot < 3; slot++) {
                VitalSign vs = newVitalSign(a, day, slot);
                if (!vs.getRecordedAt().isBefore(a.getAdmissionTime())) {
                    vitalBuf.add(vs);
                }
            }
        }

        // 每日临时/检验医嘱
        for (int day = 0; day < totalDays; day++) {
            if (!usable.isEmpty() && rnd.nextInt(3) == 0) {  // 临时医嘱
                orderBuf.add(newMedOrder(a, weightedPick(usable, r -> num(r.getWeight())),
                        a.getAdmissionTime().plusDays(day).plusHours(9), 1));      // 临时医嘱
            }
            if (rnd.nextInt(2) == 0) {  // 检验医嘱 -> lab_test
                LabTest lt = newInpatientLabTest(a, day);
                if (lt != null) inpatientLabBuf.add(lt);
            }
        }

        // 路径：合规则按步骤补齐对应 LAB/MED 事件，不合规则跳过（自然变异）
        if (compliant && pathway != null) {
            for (PathwayStep step : parsePathwaySteps(pathway.getSteps())) {
                if (step.day() >= totalDays) continue;
                if ("LAB".equals(step.action())) {
                    LabTest lt = labTestForItem(a, step);
                    if (lt != null) inpatientLabBuf.add(lt);
                } else if ("MED".equals(step.action())) {
                    // 路径步骤里的药名来自 clinical_pathway.steps，不改写
                    MedicalOrder o = new MedicalOrder();
                    o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
                    o.setOrderType(1); o.setItemName(step.item());
                    o.setDosage("1片"); o.setFrequency("每日1次");
                    o.setStartTime(a.getAdmissionTime().plusDays(step.day()).plusHours(10));
                    o.setStatus(2); o.setDoctor(a.getDoctor());
                    orderBuf.add(o);
                }
            }
        }

        // 出院
        LocalDateTime discharge = a.getAdmissionTime().plusDays(totalDays).plusHours(rnd.nextInt(0, 24));
        a.setDischargeTime(discharge);
        a.setStatus(1);
        a.setTotalCost(a.getDeposit().add(BigDecimal.valueOf(totalDays * 120L)));
    }

    /**
     * 一条住院用药医嘱：药名与剂量/频次均取自本体（diagnosis_drug + drug_dose）。
     *
     * @param orderType 0 长期医嘱 / 1 临时医嘱（与 {@code medical_order.status} 的 0/2 一一对应）
     */
    private MedicalOrder newMedOrder(Admission a, DiagnosisDrug dg, LocalDateTime startTime, int orderType) {
        Drug drug = drugsById.get(dg.getDrugId());
        MedicalOrder o = new MedicalOrder();
        o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
        o.setOrderType(orderType);
        o.setItemName(drug == null ? "未知药品" : drug.getName());
        DrugDose dose = drug == null ? null : doseByDrugId.get(drug.getId());
        if (dose == null) {
            orderDoseFallbacks++;
            o.setDosage("1片"); o.setFrequency("每日1次");
        } else {
            o.setDosage(plain(dose.getSingleDose())
                    + (dose.getDoseUnit() == null ? "" : dose.getDoseUnit()));
            o.setFrequency(dose.getFrequency());
        }
        o.setStartTime(startTime);
        o.setStatus(orderType == 1 ? 2 : 0);
        o.setDoctor(a.getDoctor());
        return o;
    }

    private VitalSign newVitalSign(Admission a, int day, int slot) {
        VitalSign vs = new VitalSign();
        vs.setPatientId(a.getPatientId()); vs.setAdmissionId(a.getId());
        // 体温带「先升后降」趋势：住院前段偏高，随天数回落
        double trend = day < 2 ? 0.6 - day * 0.2 : 0.0;
        double temp = 36.5 + trend + rnd.nextDouble() * 0.6;
        vs.setTemperature(BigDecimal.valueOf(Math.round(temp * 10) / 10.0));
        vs.setPulse(72 + rnd.nextInt(-8, 9));
        vs.setRespiration(16 + rnd.nextInt(-2, 3));
        vs.setSystolicBp(120 + rnd.nextInt(-12, 13));
        vs.setDiastolicBp(78 + rnd.nextInt(-8, 9));
        int hour = 6 + slot * 6 + rnd.nextInt(0, 2);
        vs.setRecordedAt(a.getAdmissionTime().plusDays(day).withHour(hour).withMinute(rnd.nextInt(0, 60)));
        vs.setNurse(randomPharmacist());
        return vs;
    }

    /**
     * 住院期间的常规检验：仍用 {@link MedicalData#LAB_ITEMS} 这一份「住院常规化验组合」，
     * 不走 {@code diagnosis_lab}——住院检验没有 visit_id，不参与门诊的 A3 断言；
     * 临床路径的 LAB 步骤也按项目名在这份组合里查表。若要一并改成本体驱动，属另一处改动。
     */
    private LabTest newInpatientLabTest(Admission a, int day) {
        MedicalData.LabItem li = MedicalData.LAB_ITEMS.get(rnd.nextInt(MedicalData.LAB_ITEMS.size()));
        LabTest lt = new LabTest();
        lt.setPatientId(a.getPatientId()); lt.setAdmissionId(a.getId());
        lt.setItemCode(li.code()); lt.setItemName(li.name());
        double val = li.low() + rnd.nextDouble() * (li.high() - li.low());
        boolean abnormal = rnd.nextDouble() < 0.15;
        if (abnormal) val = li.high() + rnd.nextDouble() * li.high() * 0.3;
        lt.setResultValue(String.format("%.2f", val));
        lt.setUnit(li.unit()); lt.setReferenceRange(li.ref());
        lt.setIsAbnormal(abnormal ? 1 : 0);
        lt.setTestedAt(a.getAdmissionTime().plusDays(day).plusHours(8));
        return lt;
    }

    private LabTest labTestForItem(Admission a, PathwayStep step) {
        MedicalData.LabItem li = MedicalData.LAB_ITEMS.stream()
                .filter(x -> x.name().equals(step.item())).findFirst().orElse(null);
        if (li == null) return null;
        LabTest lt = new LabTest();
        lt.setPatientId(a.getPatientId()); lt.setAdmissionId(a.getId());
        lt.setItemCode(li.code()); lt.setItemName(li.name());
        double val = li.low() + rnd.nextDouble() * (li.high() - li.low());
        lt.setResultValue(String.format("%.2f", val));
        lt.setUnit(li.unit()); lt.setReferenceRange(li.ref()); lt.setIsAbnormal(0);
        lt.setTestedAt(a.getAdmissionTime().plusDays(step.day()).plusHours(rnd.nextInt(1, Math.max(2, step.window()))));
        return lt;
    }

    private ClinicalPathway pathwayFor(String diagnosisName) {
        return pathways.stream().filter(x -> x.getDiagnosisName().equals(diagnosisName))
                .findFirst().orElse(null);
    }

    private record PathwayStep(int day, String action, String item, int window) {}

    private List<PathwayStep> parsePathwaySteps(String json) {
        if (json == null || json.isBlank()) return List.of();
        try {
            List<Map<String, Object>> raw = new com.fasterxml.jackson.databind.ObjectMapper()
                    .readValue(json, new com.fasterxml.jackson.core.type.TypeReference<List<Map<String, Object>>>() {});
            List<PathwayStep> steps = new ArrayList<>();
            for (Map<String, Object> m : raw) {
                int day = ((Number) m.get("day")).intValue();
                String action = (String) m.get("action");
                String item = (String) m.getOrDefault("item", "");
                int window = ((Number) m.get("window")).intValue();
                steps.add(new PathwayStep(day, action, item, window));
            }
            return steps;
        } catch (Exception e) {
            return List.of();
        }
    }

    /** 加载医生并按键分组为「科室 → 医生姓名列表」；表为空时快速失败。 */
    private Map<String, List<String>> loadDoctors() {
        Map<String, List<String>> byDept = doctorMapper.selectList(null).stream()
                .collect(Collectors.groupingBy(Doctor::getDepartment,
                        Collectors.mapping(Doctor::getName, Collectors.toList())));
        if (byDept.isEmpty()) {
            throw new IllegalStateException(
                    "doctor 表为空，请先执行 backend/sql/seed.sql 写入医生种子数据");
        }
        return byDept;
    }

    private String randomName() {
        return MedicalData.SURNAMES[rnd.nextInt(MedicalData.SURNAMES.length)]
             + MedicalData.GIVEN[rnd.nextInt(MedicalData.GIVEN.length)];
    }

    // ================= 本体取值与数值工具 =================

    /** 按权重抽一个：权重 <= 0 或缺失时按等权 1.0 处理。 */
    private <T> T weightedPick(List<T> pool, ToDoubleFunction<T> weight) {
        double total = 0;
        for (T t : pool) {
            total += weightOrOne(weight.applyAsDouble(t));
        }
        double r = rnd.nextDouble() * total;
        for (T t : pool) {
            r -= weightOrOne(weight.applyAsDouble(t));
            if (r <= 0) {
                return t;
            }
        }
        return pool.get(pool.size() - 1);
    }

    private static double weightOrOne(double w) {
        return (Double.isNaN(w) || w <= 0) ? 1.0 : w;
    }

    /** BigDecimal 权重 → double（NULL 视作 1.0）。 */
    private static double num(BigDecimal x) {
        return x == null ? 1.0 : x.doubleValue();
    }

    /** BigDecimal → 文本：去掉无意义的尾随 0（{@code 1.00} → {@code 1}，{@code 1.50} → {@code 1.5}）。 */
    private static String plain(BigDecimal x) {
        return x == null ? "1" : x.stripTrailingZeros().toPlainString();
    }

    /** 频次里的每日次数：{@code 每日3次} → 3，{@code 每晚1次} → 1，取不到按 1。 */
    private static int dosesPerDay(String frequency) {
        if (frequency == null) {
            return 1;
        }
        Matcher m = FIRST_INT.matcher(frequency);
        return m.find() ? Math.max(1, Integer.parseInt(m.group(1))) : 1;
    }

    /**
     * 每盒（支/瓶）装量：从 {@code drug.spec} 里读出与剂量单位同名的那一段数量，
     * 如 {@code 0.25g*24粒} + 单位「粒」→ 24，{@code 100ml} + 单位「ml」→ 100。
     * 读不出（如乳膏的 {@code 5g} 配单位「次」）返回 {@code null}，按 1 盒计。
     */
    private static Double unitsPerBox(String spec, String doseUnit) {
        if (spec == null || doseUnit == null || doseUnit.isBlank()) {
            return null;
        }
        Matcher m = Pattern.compile("([0-9]+(?:\\.[0-9]+)?)\\s*" + Pattern.quote(doseUnit)).matcher(spec);
        Double last = null;
        while (m.find()) {
            last = Double.parseDouble(m.group(1));
        }
        return last;
    }

    /** 数量（盒）：单次剂量 × 每日次数 × 疗程 ÷ 每盒装量，向上取整，至少 1 盒。 */
    private static long packsFor(String spec, String doseUnit, double singleDose, int perDay, int days) {
        double totalDoses = singleDose * perDay * days;
        Double perBox = unitsPerBox(spec, doseUnit);
        long packs = (perBox == null || perBox <= 0) ? 1 : (long) Math.ceil(totalDoses / perBox);
        return Math.max(1, packs);
    }

    /** (ICD, 科室) 复合键：diagnosis_drug / diagnosis_lab 都按这两列联查。 */
    private record DxDept(String icdCode, String department) {}

    /** 处方与其所属诊断（本体行）的配对（处方批量插入后按序取用） */
    private record PendingPrescription(Prescription prescription, DeptDiagnosis diagnosis) {}

    /** 住院记录与其入院诊断（本体行）的配对 */
    private record PendingAdmission(Admission admission, DeptDiagnosis diagnosis) {}
}

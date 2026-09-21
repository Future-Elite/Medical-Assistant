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
import java.util.stream.Collectors;

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
    /** 清空时保留 drug / rule_config 字典 */
    private static final List<String> DATA_TABLES =
            List.of("patient", "visit", "diagnosis", "prescription", "prescription_item", "lab_test",
                    "charge", "dispense", "admission", "medical_order", "vital_sign");
    /** 挂号费固定金额 */
    private static final BigDecimal REGISTRATION_FEE = new BigDecimal("10.00");
    /** 每检验项费用 */
    private static final BigDecimal LAB_FEE = new BigDecimal("15.00");
    /** 处方违规注入概率 */
    static final double VIOLATION_RATE = 0.03;
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

    private final PatientMapper patientMapper;
    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final LabTestMapper labTestMapper;
    private final DrugMapper drugMapper;
    private final RuleConfigMapper ruleConfigMapper;
    private final DoctorMapper doctorMapper;
    private final JdbcTemplate jdbcTemplate;
    private final ChargeMapper chargeMapper;
    private final DispenseMapper dispenseMapper;
    private final RationalRuleMapper rationalRuleMapper;
    private final AdmissionMapper admissionMapper;
    private final MedicalOrderMapper medicalOrderMapper;
    private final VitalSignMapper vitalSignMapper;
    private final ClinicalPathwayMapper pathwayMapper;

    /** 生成前清空八张业务数据表，使重复执行等价于「重新生成」（默认开启） */
    @Value("${generator.clear-before-run:true}")
    private boolean clearBeforeRun = true;

    private Map<Long, Drug> drugsById;
    private List<MedicalData.DiagnosisRule> rules;
    private Map<String, List<String>> doctorsByDept;
    private ThreadLocalRandom rnd;
    private List<RationalRule> interactionRules;
    private Map<Long, RationalRule> doseRulesByDrug;
    private long chargeCount, dispenseCount;
    private List<ClinicalPathway> pathways;
    private long admissionCount, orderCount, vitalCount;

    /** 生成 patientCount 名患者及各自就诊链，返回统计信息 */
    public Map<String, Long> generate(long patientCount) {
        long start = System.currentTimeMillis();
        rnd = ThreadLocalRandom.current();
        if (clearBeforeRun) {
            clearDataTables();
        }
        drugsById = drugMapper.selectList(null).stream()
                .collect(Collectors.toMap(Drug::getId, d -> d));
        rules = loadRules();
        doctorsByDept = loadDoctors();
        interactionRules = new ArrayList<>();
        doseRulesByDrug = new HashMap<>();
        for (RationalRule r : rationalRuleMapper.selectList(
                new LambdaQueryWrapper<RationalRule>().eq(RationalRule::getEnabled, 1))) {
            if (r.getRuleType() != null && r.getRuleType() == 1
                    && r.getDrugAId() != null && r.getDrugBId() != null) {
                interactionRules.add(r);
            } else if (r.getRuleType() != null && r.getRuleType() == 2 && r.getDrugId() != null) {
                doseRulesByDrug.put(r.getDrugId(), r);
            }
        }
        chargeCount = 0;
        dispenseCount = 0;
        pathways = pathwayMapper.selectList(
                new LambdaQueryWrapper<ClinicalPathway>().eq(ClinicalPathway::getEnabled, 1));
        admissionCount = 0; orderCount = 0; vitalCount = 0;

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
        stat.put("elapsed_ms", System.currentTimeMillis() - start);
        return stat;
    }

    /** 幂等：清空八张业务数据表（不动 drug / rule_config） */
    private void clearDataTables() {
        for (String table : DATA_TABLES) {
            jdbcTemplate.execute("TRUNCATE TABLE " + table);
        }
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
        for (Visit v : visitBuf) {
            // 诊断（从规则表随机选）
            MedicalData.DiagnosisRule rule = rules.get(rnd.nextInt(rules.size()));
            Diagnosis d = new Diagnosis();
            d.setPatientId(v.getPatientId()); d.setVisitId(v.getId());
            d.setIcdCode(rule.code()); d.setDiagnosisName(rule.name());
            d.setDoctor(v.getDoctor()); d.setDiagnosedAt(v.getVisitTime().plusMinutes(rnd.nextInt(5, 20)));
            diagBuf.add(d);

            // 处方（诊断对应药品，保证诊断↔用药合理）
            Prescription pr = new Prescription();
            pr.setPatientId(v.getPatientId()); pr.setVisitId(v.getId());
            pr.setDoctor(v.getDoctor()); pr.setPrescribedAt(d.getDiagnosedAt().plusMinutes(rnd.nextInt(2, 10)));
            pr.setTotalAmount(BigDecimal.ZERO);
            presBuf.add(new PendingPrescription(pr, rule));
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
            List<PrescriptionItem> its = buildItems(pp.rule());
            if (rnd.nextDouble() < VIOLATION_RATE) {
                injectViolation(its, pp.rule());
            }
            for (PrescriptionItem it : its) {
                it.setPrescriptionId(pr.getId());
            }
            // total 重算（违规注入后 drugId 可能指向规则外药品；缺药/缺价一律按 0 计，杜绝 NPE）
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

            List<LabTest> labs = newLabTests(pr);
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
        List<Admission> admissionBuf = new ArrayList<>();
        List<MedicalOrder> orderBuf = new ArrayList<>();
        List<VitalSign> vitalBuf = new ArrayList<>();
        List<LabTest> inpatientLabBuf = new ArrayList<>();
        for (Patient p : patients) {
            if (rnd.nextDouble() >= INPATIENT_RATIO) {
                continue;
            }
            admissionBuf.addAll(newAdmissions(p, lastVisitByPatient.get(p.getId())));
        }
        admissionMapper.insert(admissionBuf);      // 回填 admission.id
        for (Admission a : admissionBuf) {
            generateInpatientEvents(a, orderBuf, vitalBuf, inpatientLabBuf);
        }
        medicalOrderMapper.insert(orderBuf);
        vitalSignMapper.insert(vitalBuf);
        labTestMapper.insert(inpatientLabBuf);
        admissionMapper.updateById(admissionBuf);  // 回写出院字段 dischargeTime/totalCost/status
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

    private List<LabTest> newLabTests(Prescription pr) {
        int labCount = rnd.nextInt(1, 4);
        List<LabTest> labs = new ArrayList<>(labCount);
        for (int k = 0; k < labCount; k++) {
            MedicalData.LabItem li = MedicalData.LAB_ITEMS.get(rnd.nextInt(MedicalData.LAB_ITEMS.size()));
            LabTest lt = new LabTest();
            lt.setPatientId(pr.getPatientId()); lt.setVisitId(pr.getVisitId());
            lt.setItemCode(li.code()); lt.setItemName(li.name());
            double val = li.low() + rnd.nextDouble() * (li.high() - li.low());
            boolean abnormal = rnd.nextDouble() < 0.15;
            if (abnormal) val = li.high() + rnd.nextDouble() * li.high() * 0.3;
            lt.setResultValue(String.format("%.2f", val));
            lt.setUnit(li.unit()); lt.setReferenceRange(li.ref());
            lt.setIsAbnormal(abnormal ? 1 : 0);
            lt.setTestedAt(pr.getPrescribedAt().plusMinutes(rnd.nextInt(20, 90)));
            labs.add(lt);
        }
        return labs;
    }

    /** 从该诊断的规则药品生成处方明细（不含 prescription_id 与金额，由调用方回填）。 */
    private List<PrescriptionItem> buildItems(MedicalData.DiagnosisRule rule) {
        List<PrescriptionItem> items = new ArrayList<>();
        for (Long drugId : pickDrugIds(rule)) {
            Drug drug = drugsById.get(drugId);
            if (drug == null) {
                throw new IllegalStateException("rule_config 的诊断 " + rule.code()
                        + " 引用了不存在的 drug_id=" + drugId + "，请修正种子数据");
            }
            PrescriptionItem it = new PrescriptionItem();
            it.setDrugId(drug.getId()); it.setDrugName(drug.getName());
            it.setDosage(rnd.nextInt(1, 3) + "片"); it.setFrequency("每日" + rnd.nextInt(1, 3) + "次");
            it.setDays(rnd.nextInt(3, 15)); it.setQuantity(BigDecimal.valueOf(rnd.nextInt(1, 4)));
            it.setUnit(drug.getUnit());
            items.add(it);
        }
        return items;
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

    /** 构造一名患者的住院记录（1-3 次），只填 Admission 字段；医嘱/体征/检验在回填 id 之后生成。 */
    private List<Admission> newAdmissions(Patient p, LocalDateTime lastVisit) {
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

        List<Admission> list = new ArrayList<>(count);
        for (int i = 0; i < count; i++) {
            ClinicalPathway pathway = pathways.isEmpty() ? null
                    : pathways.get(rnd.nextInt(pathways.size()));
            String diagnosis = pathway != null ? pathway.getDiagnosisName()
                    : randomDiagnosisName();
            String department = randomDepartment();
            String doctor = doctorsByDept.get(department)
                    .get(rnd.nextInt(doctorsByDept.get(department).size()));

            Admission a = new Admission();
            a.setPatientId(p.getId());
            a.setAdmissionNo("A" + p.getId() + "-" + (i + 1));
            a.setDepartment(department); a.setDoctor(doctor);
            a.setBedNo(rnd.nextInt(1, 60) + "床");
            a.setDiagnosis(diagnosis);
            a.setAdmissionTime(first.plusDays((long) i * ADMISSION_GAP_DAYS));
            a.setDeposit(BigDecimal.valueOf(rnd.nextInt(5, 21) * 100));
            a.setStatus(0);
            list.add(a);
        }
        return list;
    }

    /** 生成一次住院的医嘱/生命体征/住院检验，并回写出院字段（dischargeTime/totalCost/status）。 */
    private void generateInpatientEvents(Admission a, List<MedicalOrder> orderBuf,
                                         List<VitalSign> vitalBuf, List<LabTest> inpatientLabBuf) {
        ClinicalPathway pathway = pathwayFor(a.getDiagnosis());
        int totalDays = rnd.nextInt(MIN_STAY_DAYS, MAX_STAY_DAYS + 1);
        boolean compliant = pathway != null && rnd.nextDouble() < PATHWAY_COMPLIANCE_RATE;

        // 长期医嘱（首日开，执行至出院）
        int longOrderCount = rnd.nextInt(1, 4);
        for (int k = 0; k < longOrderCount; k++) {
            MedicalOrder o = new MedicalOrder();
            o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
            o.setOrderType(0);
            o.setItemName(randomDrugName());
            o.setDosage("1片"); o.setFrequency("每日" + rnd.nextInt(1, 3) + "次");
            o.setStartTime(a.getAdmissionTime().plusMinutes(rnd.nextInt(5, 60)));
            o.setStatus(0); o.setDoctor(a.getDoctor());
            orderBuf.add(o);
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
            if (rnd.nextInt(3) == 0) {  // 临时医嘱
                MedicalOrder o = new MedicalOrder();
                o.setPatientId(a.getPatientId()); o.setAdmissionId(a.getId());
                o.setOrderType(1); o.setItemName(randomDrugName());
                o.setDosage("1片"); o.setFrequency("每日1次");
                o.setStartTime(a.getAdmissionTime().plusDays(day).plusHours(9));
                o.setStatus(2); o.setDoctor(a.getDoctor());
                orderBuf.add(o);
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

    private String randomDrugName() {
        return drugsById.values().stream().map(Drug::getName)
                .toList().get(rnd.nextInt(drugsById.size()));
    }

    private String randomDiagnosisName() {
        return rules.get(rnd.nextInt(rules.size())).name();
    }

    private String randomDepartment() {
        return new ArrayList<>(doctorsByDept.keySet()).get(rnd.nextInt(doctorsByDept.size()));
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

    /** 以固定方式注入一类违规，返回违规类型（供测试断言）；不注入返回 null。 */
    String injectViolation(List<PrescriptionItem> items, MedicalData.DiagnosisRule rule) {
        if (interactionRules.isEmpty() && doseRulesByDrug.isEmpty()) {
            return null;
        }
        int type = rnd.nextInt(3);
        if (type == 0 && !doseRulesByDrug.isEmpty()) {
            // 剂量：任取一条剂量规则，把对应药加进明细并设高剂量
            RationalRule r = doseRulesByDrug.values().iterator().next();
            PrescriptionItem it = items.isEmpty() ? null : items.get(0);
            if (it != null) {
                it.setDrugId(r.getDrugId());
                Drug d = drugsById.get(r.getDrugId());
                if (d != null) it.setDrugName(d.getName());
                it.setDosage("4片"); it.setFrequency("每日3次");
                return "DOSE";
            }
        } else if (type == 1 && !interactionRules.isEmpty()) {
            // 相互作用：直接用一条相互作用规则的两个药作为两条明细
            RationalRule r = interactionRules.get(rnd.nextInt(interactionRules.size()));
            items.clear();
            for (Long drugId : List.of(r.getDrugAId(), r.getDrugBId())) {
                Drug d = drugsById.get(drugId);
                if (d == null) return null;
                PrescriptionItem it = new PrescriptionItem();
                it.setDrugId(d.getId()); it.setDrugName(d.getName());
                it.setDosage("1片"); it.setFrequency("每日1次");
                it.setDays(7); it.setQuantity(BigDecimal.ONE); it.setUnit(d.getUnit());
                items.add(it);
            }
            return "INTERACTION";
        } else {
            // 适应症：把首条明细换成规则外药品
            List<Long> allowed = rule.drugIds();
            List<Drug> others = drugsById.values().stream()
                    .filter(d -> !allowed.contains(d.getId())).toList();
            if (!others.isEmpty() && !items.isEmpty()) {
                Drug d = others.get(rnd.nextInt(others.size()));
                PrescriptionItem it = items.get(0);
                it.setDrugId(d.getId()); it.setDrugName(d.getName());
                return "INDICATION";
            }
        }
        return null;
    }

    /** 从该诊断的规则药品中随机取 1-3 种 */
    private List<Long> pickDrugIds(MedicalData.DiagnosisRule rule) {
        int drugCount = rnd.nextInt(1, Math.min(3, rule.drugIds().size()) + 1);
        List<Long> chosen = new ArrayList<>(rule.drugIds());
        Collections.shuffle(chosen, rnd);
        return chosen.subList(0, drugCount);
    }

    private List<MedicalData.DiagnosisRule> loadRules() {
        List<MedicalData.DiagnosisRule> loaded = ruleConfigMapper
                .selectList(new LambdaQueryWrapper<RuleConfig>().eq(RuleConfig::getEnabled, 1))
                .stream()
                .map(r -> new MedicalData.DiagnosisRule(r.getDiagnosisCode(), r.getDiagnosisName(),
                        parseDrugIds(r.getDrugIds())))
                .filter(r -> !r.drugIds().isEmpty())
                .toList();
        if (loaded.isEmpty()) {
            throw new IllegalStateException(
                    "rule_config 中没有可用的诊断用药规则（enabled=1 且 drug_ids 非空），请先执行 backend/sql/seed.sql");
        }
        return loaded;
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

    /** drug_ids 可为 NULL / 空串 / 逗号列表 */
    private List<Long> parseDrugIds(String drugIds) {
        if (drugIds == null || drugIds.isBlank()) {
            return List.of();
        }
        return Arrays.stream(drugIds.split(","))
                .map(String::trim)
                .filter(s -> !s.isEmpty())
                .map(Long::parseLong)
                .toList();
    }

    private String randomName() {
        return MedicalData.SURNAMES[rnd.nextInt(MedicalData.SURNAMES.length)]
             + MedicalData.GIVEN[rnd.nextInt(MedicalData.GIVEN.length)];
    }

    /** 处方与其所属诊断规则的配对（处方批量插入后按序取用） */
    private record PendingPrescription(Prescription prescription, MedicalData.DiagnosisRule rule) {}
}

package com.his.generator;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.util.ReflectionTestUtils;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.Period;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BiConsumer;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.Mockito.*;

/**
 * 生成器行为测试。
 *
 * <p>本测试用的「医学本体」是一份最小夹具（2 个科室 / 3 个诊断 / 3 种药），
 * 所有「诊断随科室、用药随（诊断,科室）、剂量随药品」的断言都对着这份夹具做，
 * 因此断言失败即代表生成器没有真正按本体取材。
 */
class DataGeneratorServiceTest {

    // ===== 本体夹具：药品（id 与 drug / drug_dose 对齐）=====
    private static final long DRUG_AMOX = 1L;      // 阿莫西林胶囊 盒 ¥18.50 spec 0.25g*24粒 → 每盒 24 粒
    private static final long DRUG_GANMAO = 2L;    // 感冒灵颗粒   盒 ¥12.00 spec 10g*9袋  → 每盒 9 袋
    private static final long DRUG_IBUPROFEN = 3L; // 布洛芬缓释胶囊 盒 ¥15.80 spec 0.3g*20粒 → 每盒 20 粒

    // ===== 本体夹具：诊断 =====
    private static final String ICD_URI = "J06.9";    // 上呼吸道感染（内科）
    private static final String ICD_HTN = "I10";      // 原发性高血压（内科）
    private static final String ICD_WOUND = "S61.0";  // 手部开放性伤口（外科）
    private static final Map<String, String> DX_NAME = Map.of(
            ICD_URI, "上呼吸道感染", ICD_HTN, "原发性高血压", ICD_WOUND, "手部开放性伤口");
    private static final Map<String, String> DX_DEPT = Map.of(
            ICD_URI, "内科", ICD_HTN, "内科", ICD_WOUND, "外科");
    /** 科室 → 该科室本体里可诊断的 ICD */
    private static final Map<String, Set<String>> DEPT_DX = Map.of(
            "内科", Set.of(ICD_URI, ICD_HTN),
            "外科", Set.of(ICD_WOUND));
    /** (ICD|科室) → 本体用药池 */
    private static final Map<String, Set<Long>> DRUG_POOL = Map.of(
            ICD_URI + "|内科", Set.of(DRUG_AMOX, DRUG_GANMAO, DRUG_IBUPROFEN),
            ICD_HTN + "|内科", Set.of(DRUG_IBUPROFEN),
            ICD_WOUND + "|外科", Set.of(DRUG_GANMAO));
    /** (ICD|科室) → 本体一线药（line=1） */
    private static final Map<String, Set<Long>> FIRST_LINE = Map.of(
            ICD_URI + "|内科", Set.of(DRUG_AMOX, DRUG_GANMAO),
            ICD_HTN + "|内科", Set.of(DRUG_IBUPROFEN),
            ICD_WOUND + "|外科", Set.of(DRUG_GANMAO));
    /** (ICD|科室) → 本体检验池 */
    private static final Map<String, Set<String>> LAB_POOL = Map.of(
            ICD_URI + "|内科", Set.of("BLD-WBC", "CXRAY"),
            ICD_HTN + "|内科", Set.of("GLU-F"),
            ICD_WOUND + "|外科", Set.of("BLD-WBC"));

    private final List<Patient> insertedPatients = new ArrayList<>();
    private final List<Visit> insertedVisits = new ArrayList<>();
    private final List<Diagnosis> insertedDiagnoses = new ArrayList<>();
    private final List<Prescription> insertedPrescriptions = new ArrayList<>();
    private final List<PrescriptionItem> insertedItems = new ArrayList<>();
    private final List<LabTest> insertedLabs = new ArrayList<>();
    private final List<Admission> insertedAdmissions = new ArrayList<>();
    private final List<MedicalOrder> insertedOrders = new ArrayList<>();
    private final List<VitalSign> insertedVitals = new ArrayList<>();

    private DeptDiagnosisMapper deptDiagnosisMapper;
    private DiagnosisDrugMapper diagnosisDrugMapper;
    private DiagnosisLabMapper diagnosisLabMapper;
    private DrugDoseMapper drugDoseMapper;
    private DoctorMapper doctorMapper;
    private PrescriptionMapper prescriptionMapper;
    private JdbcTemplate jdbcTemplate;
    private final AtomicLong idSeq = new AtomicLong();

    private DataGeneratorService buildService() {
        PatientMapper pm = mock(PatientMapper.class);
        VisitMapper vm = mock(VisitMapper.class);
        DiagnosisMapper dm = mock(DiagnosisMapper.class);
        prescriptionMapper = mock(PrescriptionMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        LabTestMapper lm = mock(LabTestMapper.class);
        DrugMapper drugM = mock(DrugMapper.class);
        doctorMapper = mock(DoctorMapper.class);
        jdbcTemplate = mock(JdbcTemplate.class);
        ChargeMapper chargeM = mock(ChargeMapper.class);
        DispenseMapper dispenseM = mock(DispenseMapper.class);
        AdmissionMapper admissionMapper = mock(AdmissionMapper.class);
        MedicalOrderMapper medicalOrderMapper = mock(MedicalOrderMapper.class);
        VitalSignMapper vitalSignMapper = mock(VitalSignMapper.class);
        ClinicalPathwayMapper pathwayMapper = mock(ClinicalPathwayMapper.class);
        deptDiagnosisMapper = mock(DeptDiagnosisMapper.class);
        diagnosisDrugMapper = mock(DiagnosisDrugMapper.class);
        diagnosisLabMapper = mock(DiagnosisLabMapper.class);
        drugDoseMapper = mock(DrugDoseMapper.class);

        Doctor doc1 = new Doctor(); doc1.setId(1L); doc1.setName("李建华"); doc1.setDepartment("内科");
        Doctor doc2 = new Doctor(); doc2.setId(2L); doc2.setName("杨文博"); doc2.setDepartment("外科");
        when(doctorMapper.selectList(any())).thenReturn(List.of(doc1, doc2));

        Drug d1 = new Drug(); d1.setId(DRUG_AMOX); d1.setName("阿莫西林胶囊"); d1.setPrice(new BigDecimal("18.50"));
        d1.setUnit("盒"); d1.setSpec("0.25g*24粒");
        Drug d2 = new Drug(); d2.setId(DRUG_GANMAO); d2.setName("感冒灵颗粒"); d2.setPrice(new BigDecimal("12.00"));
        d2.setUnit("盒"); d2.setSpec("10g*9袋");
        Drug d3 = new Drug(); d3.setId(DRUG_IBUPROFEN); d3.setName("布洛芬缓释胶囊"); d3.setPrice(new BigDecimal("15.80"));
        d3.setUnit("盒"); d3.setSpec("0.3g*20粒");
        when(drugM.selectList(any())).thenReturn(List.of(d1, d2, d3));

        // ===== 本体夹具 =====
        when(deptDiagnosisMapper.selectList(any())).thenReturn(List.of(
                deptDx("内科", ICD_URI, "上呼吸道感染", 1.0),
                deptDx("内科", ICD_HTN, "原发性高血压", 3.0),
                deptDx("外科", ICD_WOUND, "手部开放性伤口", 1.0)));
        when(diagnosisDrugMapper.selectList(any())).thenReturn(List.of(
                dxDrug(ICD_URI, "内科", DRUG_AMOX, 1, 1.0),
                dxDrug(ICD_URI, "内科", DRUG_GANMAO, 1, 0.6),
                dxDrug(ICD_URI, "内科", DRUG_IBUPROFEN, 2, 0.7),
                dxDrug(ICD_HTN, "内科", DRUG_IBUPROFEN, 1, 1.0),
                dxDrug(ICD_WOUND, "外科", DRUG_GANMAO, 1, 1.0),
                // 术后专用条目：没有术后场景，生成器必须完全不使用
                dxPostop(ICD_WOUND, "外科", DRUG_AMOX, 1, 0.9)));
        when(diagnosisLabMapper.selectList(any())).thenReturn(List.of(
                dxLab(ICD_URI, "内科", "BLD-WBC", "白细胞计数", "10^9/L", "4.0-10.0", "HIGH", 0.10),
                dxLab(ICD_URI, "内科", "CXRAY", "胸部X线", "—", "正常", "HIGH", 0.15),
                dxLab(ICD_HTN, "内科", "GLU-F", "空腹血糖", "mmol/L", "3.9-6.1", "HIGH", 0.10),
                dxLab(ICD_WOUND, "外科", "BLD-WBC", "白细胞计数", "10^9/L", "4.0-10.0", "HIGH", 0.50)));
        when(drugDoseMapper.selectList(any())).thenReturn(List.of(
                dose(DRUG_AMOX, 1, "粒", "每日3次", 5, 7),
                dose(DRUG_GANMAO, 1, "袋", "每日3次", 3, 7),
                dose(DRUG_IBUPROFEN, 1, "粒", "每日2次", 7, 14)));

        // 一条上呼吸道感染路径（steps JSON 与既有测试一致）
        ClinicalPathway cp = new ClinicalPathway();
        cp.setDiagnosisName("上呼吸道感染");
        cp.setSteps("[{\"day\":0,\"action\":\"LAB\",\"item\":\"白细胞计数\",\"window\":24},{\"day\":1,\"action\":\"MED\",\"item\":\"阿莫西林\",\"window\":24},{\"day\":3,\"action\":\"DISCHARGE\",\"window\":24}]");
        cp.setEnabled(1);
        when(pathwayMapper.selectList(any())).thenReturn(List.of(cp));

        stubBatchInsert(pm, insertedPatients, Patient::setId);
        stubBatchInsert(vm, insertedVisits, Visit::setId);
        stubBatchInsert(dm, insertedDiagnoses, Diagnosis::setId);
        stubBatchInsert(prescriptionMapper, insertedPrescriptions, Prescription::setId);
        stubBatchInsert(im, insertedItems, PrescriptionItem::setId);
        stubBatchInsert(lm, insertedLabs, LabTest::setId);
        stubBatchInsert(chargeM, null, Charge::setId);
        stubBatchInsert(dispenseM, null, Dispense::setId);
        stubBatchInsert(admissionMapper, insertedAdmissions, Admission::setId);
        stubBatchInsert(medicalOrderMapper, insertedOrders, MedicalOrder::setId);
        stubBatchInsert(vitalSignMapper, insertedVitals, VitalSign::setId);

        return new DataGeneratorService(pm, vm, dm, prescriptionMapper, im, lm, drugM,
                doctorMapper, jdbcTemplate, chargeM, dispenseM,
                admissionMapper, medicalOrderMapper, vitalSignMapper, pathwayMapper,
                deptDiagnosisMapper, diagnosisDrugMapper, diagnosisLabMapper, drugDoseMapper);
    }

    // ===== 夹具构造帮手 =====
    private static DeptDiagnosis deptDx(String dept, String icd, String name, double weight) {
        DeptDiagnosis d = new DeptDiagnosis();
        d.setDepartment(dept); d.setIcdCode(icd); d.setDiagnosisName(name);
        d.setGender(0); d.setMinAge(0); d.setMaxAge(89);
        d.setChronic(0); d.setAdmissionEligible(0); d.setWeight(BigDecimal.valueOf(weight));
        return d;
    }

    private static DiagnosisDrug dxDrug(String icd, String dept, long drugId, int line, double weight) {
        DiagnosisDrug d = new DiagnosisDrug();
        d.setIcdCode(icd); d.setDepartment(dept); d.setDrugId(drugId);
        d.setLine(line); d.setWeight(BigDecimal.valueOf(weight)); d.setPostop(0);
        return d;
    }

    private static DiagnosisDrug dxPostop(String icd, String dept, long drugId, int line, double weight) {
        DiagnosisDrug d = dxDrug(icd, dept, drugId, line, weight);
        d.setPostop(1);
        return d;
    }

    private static DiagnosisLab dxLab(String icd, String dept, String code, String name, String unit,
                                      String ref, String direction, double prob) {
        DiagnosisLab d = new DiagnosisLab();
        d.setIcdCode(icd); d.setDepartment(dept); d.setItemCode(code); d.setItemName(name);
        d.setUnit(unit); d.setRefRange(ref); d.setAbnormalDirection(direction);
        d.setAbnormalProb(BigDecimal.valueOf(prob)); d.setWeight(BigDecimal.ONE); d.setRequired(0);
        return d;
    }

    private static DrugDose dose(long drugId, double single, String unit, String freq, int min, int max) {
        DrugDose d = new DrugDose();
        d.setDrugId(drugId); d.setSingleDose(BigDecimal.valueOf(single)); d.setDoseUnit(unit);
        d.setFrequency(freq); d.setMaxDailyDose(BigDecimal.valueOf(10)); d.setDaysMin(min); d.setDaysMax(max);
        return d;
    }

    /** 模拟 MyBatis-Plus 批量插入：逐条回填自增 id，并按需收集到 sink */
    private <T> void stubBatchInsert(BaseMapper<T> mapper, List<T> sink, BiConsumer<T, Long> idSetter) {
        doAnswer(inv -> {
            List<T> batch = inv.getArgument(0);
            for (T e : batch) {
                idSetter.accept(e, idSeq.incrementAndGet());
                if (sink != null) {
                    sink.add(e);
                }
            }
            return List.of();
        }).when(mapper).insert(anyList());
    }

    private Map<Long, List<Visit>> visitsByPatient() {
        Map<Long, List<Visit>> byPatient = new HashMap<>();
        for (Visit v : insertedVisits) {
            byPatient.computeIfAbsent(v.getPatientId(), k -> new ArrayList<>()).add(v);
        }
        return byPatient;
    }

    private Map<Long, Visit> visitById() {
        return insertedVisits.stream().collect(Collectors.toMap(Visit::getId, v -> v));
    }

    private Map<Long, Diagnosis> diagnosisByVisit() {
        return insertedDiagnoses.stream().collect(Collectors.toMap(Diagnosis::getVisitId, d -> d, (a, b) -> a));
    }

    /** 处方 id → 「ICD|科室」（来自该处方所属就诊的诊断与科室） */
    private Map<Long, String> dxDeptByPrescription() {
        Map<Long, Visit> vById = visitById();
        Map<Long, Diagnosis> dByVisit = diagnosisByVisit();
        Map<Long, String> out = new HashMap<>();
        for (Prescription p : insertedPrescriptions) {
            Diagnosis d = dByVisit.get(p.getVisitId());
            out.put(p.getId(), d.getIcdCode() + "|" + vById.get(p.getVisitId()).getDepartment());
        }
        return out;
    }

    private Map<Long, List<PrescriptionItem>> itemsByPrescription() {
        Map<Long, List<PrescriptionItem>> out = new HashMap<>();
        for (PrescriptionItem it : insertedItems) {
            out.computeIfAbsent(it.getPrescriptionId(), k -> new ArrayList<>()).add(it);
        }
        return out;
    }

    @Test
    void visits_are_strictly_increasing_in_time() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(3);

        assertThat(stat.get("patients")).isEqualTo(3L);
        assertThat(stat.get("visits")).isGreaterThanOrEqualTo(15L); // 3 × 最少 5 次

        Map<Long, List<Visit>> byPatient = visitsByPatient();
        assertThat(byPatient.keySet()).hasSize(3);
        for (List<Visit> visits : byPatient.values()) {
            for (int i = 1; i < visits.size(); i++) {
                assertThat(visits.get(i).getVisitTime()).isAfter(visits.get(i - 1).getVisitTime());
            }
        }
    }

    // ===================== 缺陷 1：诊断必须出自就诊科室的本体 =====================

    /** 诊断的 ICD 必须在该次就诊科室的 dept_diagnosis 里（「妇科医生看肠胃炎」的回归断言） */
    @Test
    void diagnoses_belong_to_the_department_of_their_visit() {
        DataGeneratorService svc = buildService();
        svc.generate(50);

        assertThat(insertedDiagnoses).isNotEmpty();
        Map<Long, Visit> vById = visitById();
        for (Diagnosis d : insertedDiagnoses) {
            String dept = vById.get(d.getVisitId()).getDepartment();
            assertThat(DEPT_DX.get(dept))
                    .as("就诊科室 %s 的诊断 %s 不在该科室本体里", dept, d.getIcdCode())
                    .contains(d.getIcdCode());
        }
    }

    /** 诊断名必须与本体 dept_diagnosis 的行一致，而不是从全局池里瞎抽 */
    @Test
    void diagnosis_names_come_from_the_ontology_row() {
        DataGeneratorService svc = buildService();
        svc.generate(20);

        for (Diagnosis d : insertedDiagnoses) {
            assertThat(DX_NAME.get(d.getIcdCode())).isEqualTo(d.getDiagnosisName());
            assertThat(DX_DEPT.get(d.getIcdCode()))
                    .isEqualTo(visitById().get(d.getVisitId()).getDepartment());
        }
    }

    // ===================== 缺陷 2：用药必须出自 (ICD, 科室) 的本体 =====================

    /** 处方药必须同时落在「该诊断 + 该科室」的 diagnosis_drug 池里；postop=1 的药绝不能被开出来 */
    @Test
    void prescribed_drugs_come_from_the_ontology_of_diagnosis_and_department() {
        DataGeneratorService svc = buildService();
        svc.generate(30);

        assertThat(insertedItems).isNotEmpty();
        Map<Long, String> dxDept = dxDeptByPrescription();
        for (PrescriptionItem it : insertedItems) {
            String key = dxDept.get(it.getPrescriptionId());
            assertThat(key).isNotNull();
            assertThat(DRUG_POOL.get(key))
                    .as("处方药 %s 不在 (%s) 的本体用药池里", it.getDrugName(), key)
                    .contains(it.getDrugId());
        }
        // 夹具里阿莫西林在 (S61.0, 外科) 是 postop=1（术后专用）；没有术后场景，绝不能开给外科就诊
        assertThat(insertedItems).noneMatch(it -> it.getDrugId() == DRUG_AMOX
                && "S61.0|外科".equals(dxDept.get(it.getPrescriptionId())));
    }

    /** line=1 的一线药优先：夹具里每个池都有一线药，因此每张处方都至少含一条一线药 */
    @Test
    void every_prescription_contains_a_first_line_drug() {
        DataGeneratorService svc = buildService();
        svc.generate(30);

        Map<Long, String> dxDept = dxDeptByPrescription();
        Map<Long, List<PrescriptionItem>> byRx = itemsByPrescription();
        for (Prescription pr : insertedPrescriptions) {
            Set<Long> firstLine = FIRST_LINE.get(dxDept.get(pr.getId()));
            assertThat(byRx.get(pr.getId()).stream().map(PrescriptionItem::getDrugId))
                    .as("处方 %s 一条一线药都没有", pr.getId())
                    .anyMatch(firstLine::contains);
        }
    }

    // ===================== 缺陷 3：检验项必须出自 (ICD, 科室) 的本体 =====================

    /** 门诊检验项必须落在「该诊断 + 该科室」的 diagnosis_lab 池里；结果值与参考区间自洽 */
    @Test
    void lab_tests_come_from_the_ontology_of_diagnosis_and_department() {
        DataGeneratorService svc = buildService();
        svc.generate(20);

        Map<Long, Visit> vById = visitById();
        Map<Long, Diagnosis> dByVisit = diagnosisByVisit();
        List<LabTest> outpatient = insertedLabs.stream().filter(x -> x.getVisitId() != null).toList();
        assertThat(outpatient).isNotEmpty();
        for (LabTest lt : outpatient) {
            Diagnosis d = dByVisit.get(lt.getVisitId());
            String key = d.getIcdCode() + "|" + vById.get(lt.getVisitId()).getDepartment();
            assertThat(LAB_POOL.get(key))
                    .as("检验项 %s 不在 (%s) 的本体检验池里", lt.getItemCode(), key)
                    .contains(lt.getItemCode());

            boolean qualitative = "CXRAY".equals(lt.getItemCode());
            assertThat(lt.getIsAbnormal()).isIn(0, 1);
            if (qualitative) {
                // 定性项：区间列存「正常」，异常时写「异常」
                assertThat(lt.getReferenceRange()).isEqualTo("正常");
                assertThat(lt.getResultValue()).isIn("正常", "异常");
                assertThat(lt.getIsAbnormal()).isEqualTo("异常".equals(lt.getResultValue()) ? 1 : 0);
            } else {
                String[] bounds = lt.getReferenceRange().split("-");
                double low = Double.parseDouble(bounds[0]);
                double high = Double.parseDouble(bounds[1]);
                double val = Double.parseDouble(lt.getResultValue());
                if (lt.getIsAbnormal() == 1) {
                    assertThat(val < low || val > high)
                            .as("异常值 %s 未越出参考区间 %s", lt.getResultValue(), lt.getReferenceRange())
                            .isTrue();
                } else {
                    assertThat(val).isBetween(low, high);
                }
            }
        }
    }

    // ===================== 缺陷 4：剂量必须出自 drug_dose =====================

    /** 剂量/频次/疗程取自 drug_dose；数量按「单次剂量 × 每日次数 × 疗程 ÷ 每盒装量」折算成盒 */
    @Test
    void dosages_and_quantities_come_from_drug_dose_ontology() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(10);

        assertThat(insertedItems).isNotEmpty();
        assertThat(stat.get("dose_fallbacks")).isZero();
        assertThat(stat.get("order_dose_fallbacks")).isZero();
        assertThat(stat.get("dose_from_drug_dose")).isEqualTo((long) insertedItems.size());

        Map<Long, String> dosage = Map.of(DRUG_AMOX, "1粒", DRUG_GANMAO, "1袋", DRUG_IBUPROFEN, "1粒");
        Map<Long, String> frequency = Map.of(DRUG_AMOX, "每日3次", DRUG_GANMAO, "每日3次", DRUG_IBUPROFEN, "每日2次");
        Map<Long, int[]> days = Map.of(DRUG_AMOX, new int[]{5, 7}, DRUG_GANMAO, new int[]{3, 7},
                DRUG_IBUPROFEN, new int[]{7, 14});
        Map<Long, Integer> perDay = Map.of(DRUG_AMOX, 3, DRUG_GANMAO, 3, DRUG_IBUPROFEN, 2);
        Map<Long, Integer> perBox = Map.of(DRUG_AMOX, 24, DRUG_GANMAO, 9, DRUG_IBUPROFEN, 20);

        for (PrescriptionItem it : insertedItems) {
            long id = it.getDrugId();
            assertThat(it.getDosage()).isEqualTo(dosage.get(id));
            assertThat(it.getFrequency()).isEqualTo(frequency.get(id));
            assertThat(it.getDays()).isBetween(days.get(id)[0], days.get(id)[1]);
            assertThat(it.getUnit()).isEqualTo("盒");
            long packs = (long) Math.ceil(1.0 * perDay.get(id) * it.getDays() / perBox.get(id));
            assertThat(it.getQuantity()).isEqualByComparingTo(BigDecimal.valueOf(Math.max(1, packs)));
        }
    }

    /** drug_dose 缺行时必须回退到旧写法，并把回退条数计入统计（不得静默） */
    @Test
    void missing_drug_dose_rows_fall_back_and_are_counted() {
        DataGeneratorService svc = buildService();
        when(drugDoseMapper.selectList(any())).thenReturn(List.of());
        Map<String, Long> stat = svc.generate(3);

        assertThat(insertedItems).isNotEmpty();
        assertThat(stat.get("dose_from_drug_dose")).isZero();
        assertThat(stat.get("dose_fallbacks")).isEqualTo((long) insertedItems.size());
        assertThat(insertedItems).allSatisfy(it -> {
            assertThat(it.getDosage()).matches("[0-9]+片");
            assertThat(it.getFrequency()).matches("每日[0-9]+次");
        });
    }

    // ===================== 缺陷 5：不存在任何违规注入机制 =====================

    /** 硬约束：生成器不得保留任何「故意注入违规」的机制（方法 / 常量 / 字段都不许有） */
    @Test
    void violation_injection_mechanism_is_gone() {
        assertThat(Arrays.stream(DataGeneratorService.class.getDeclaredMethods()).map(Method::getName))
                .noneMatch(n -> n.toLowerCase(Locale.ROOT).contains("violation"));
        assertThat(Arrays.stream(DataGeneratorService.class.getDeclaredFields()).map(Field::getName))
                .noneMatch(n -> n.toUpperCase(Locale.ROOT).contains("VIOLATION"));
    }

    // ===================== 既有不变量 =====================

    @Test
    void stats_always_report_elapsed_ms() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(1);

        assertThat(stat).containsKey("elapsed_ms");
        assertThat(stat.get("elapsed_ms")).isNotNull().isGreaterThanOrEqualTo(0L);
        assertThat(stat).containsKeys("patients", "visits", "diagnoses", "prescriptions", "prescription_items", "lab_tests");
    }

    /** 每人 5-20 次就诊；末次就诊 <= now；病史跨度落在 [1, 3] 年 */
    @Test
    void visit_count_and_history_span_stay_within_spec() {
        DataGeneratorService svc = buildService();
        svc.generate(20);
        LocalDateTime now = LocalDateTime.now();

        Map<Long, List<Visit>> byPatient = visitsByPatient();
        assertThat(byPatient).hasSize(20);
        for (List<Visit> visits : byPatient.values()) {
            assertThat(visits).hasSizeBetween(5, 20);                       // 每人 5-20 次

            LocalDateTime first = visits.get(0).getVisitTime();
            LocalDateTime last = visits.get(visits.size() - 1).getVisitTime();
            assertThat(last).isBeforeOrEqualTo(now);                        // 无未来就诊
            long spanDays = ChronoUnit.DAYS.between(first, last);
            assertThat(spanDays).isBetween(365L, 1096L);                    // 跨度 1-3 年
            // 最早一条记录不早于「3 年病史 + 末次就诊的随机尾巴」之前。
            // 尾巴随机是为了消除末次就诊的单日堆积（重要 #3），代价是极少数患者的
            // 首条记录会比 now-3y 再早至多 TAIL_SPREAD_DAYS 天。
            assertThat(first).isAfter(now.minusYears(3).minusDays(DataGeneratorService.TAIL_SPREAD_DAYS + 2L));
        }
    }

    /**
     * 重要 #3：末次就诊不得被钉在同一瞬间。
     *
     * <p>旧实现 {@code offsets[last] = windowMinutes} 配合全局共享的
     * {@code windowEnd = now - 150min}，让 10000 名患者的末次就诊落在同一小时内，
     * 造成 visit-frequency 末端一根约为基线 54 倍的单日尖峰。
     *
     * <p>样本量 200、尾巴 151 天（≈21.7 万个分钟槽位），断言取 150 个不同瞬间与
     * 30 天极差，随机误报概率可忽略。
     */
    @Test
    void last_visit_is_not_pinned_to_a_shared_instant() {
        DataGeneratorService svc = buildService();
        svc.generate(200);

        Set<LocalDateTime> instants = new HashSet<>();
        LocalDateTime oldest = null;
        LocalDateTime newest = null;
        for (List<Visit> visits : visitsByPatient().values()) {
            LocalDateTime last = visits.get(visits.size() - 1).getVisitTime();
            instants.add(last);
            if (oldest == null || last.isBefore(oldest)) {
                oldest = last;
            }
            if (newest == null || last.isAfter(newest)) {
                newest = last;
            }
        }
        assertThat(instants).hasSizeGreaterThan(150);
        assertThat(ChronoUnit.DAYS.between(oldest, newest)).isGreaterThanOrEqualTo(30L);
    }

    /** 末次就诊必须落在「now 之前、且不超过随机尾巴范围」内，保证下游时间不越过 now。 */
    @Test
    void last_visit_stays_inside_the_random_tail_window() {
        DataGeneratorService svc = buildService();
        svc.generate(50);
        LocalDateTime now = LocalDateTime.now();

        for (List<Visit> visits : visitsByPatient().values()) {
            LocalDateTime last = visits.get(visits.size() - 1).getVisitTime();
            assertThat(last).isBeforeOrEqualTo(now);
            // now - DOWNSTREAM(150min) - TAIL_SPREAD_DAYS - 1 天(日内随机分钟)
            assertThat(last).isAfter(now.minusDays(DataGeneratorService.TAIL_SPREAD_DAYS + 2L));
        }
    }

    /** 档案自洽：18 位合法身份证（GB 11643 校验位）+ 出生日期取自号码 + 性别取自顺序码奇偶。 */
    @Test
    void id_card_is_a_valid_18_digit_number_consistent_with_birth_date_and_gender() {
        DataGeneratorService svc = buildService();
        svc.generate(200);

        assertThat(insertedPatients).hasSize(200);
        for (Patient p : insertedPatients) {
            String id = p.getIdCard();
            assertThat(id).hasSize(18);
            assertThat(id).startsWith("110101");
            assertThat(id.substring(0, 17)).matches("\\d{17}");
            assertThat(id.charAt(17)).isEqualTo(expectedCheckDigit(id.substring(0, 17)));

            // 出生日期 == 号码中的 YYYYMMDD
            assertThat(id.substring(6, 14))
                    .isEqualTo(p.getBirthDate().format(DateTimeFormatter.ofPattern("yyyyMMdd")));

            // 顺序码奇偶决定性别：奇男(0) 偶女(1)
            int sequence = Integer.parseInt(id.substring(14, 17));
            assertThat(p.getGender()).isEqualTo(sequence % 2 == 1 ? 0 : 1);

            // 年龄仍落在 18-89 岁
            assertThat(Period.between(p.getBirthDate(), LocalDate.now()).getYears()).isBetween(18, 89);
        }

        // 生日不再是「所有人同月同日」：200 人的出生日期应几乎全部不同
        assertThat(insertedPatients.stream().map(Patient::getBirthDate).distinct().count()).isGreaterThan(150);
        // 性别分布不应因奇偶规则而失衡
        long male = insertedPatients.stream().filter(x -> x.getGender() == 0).count();
        assertThat(male).isBetween(60L, 140L);
    }

    /** GB 11643 校验位：sum(第 i 位 × 权重[i]) % 11 → "10X98765432"。 */
    private static char expectedCheckDigit(String body17) {
        int[] weights = {7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2};
        int sum = 0;
        for (int i = 0; i < 17; i++) {
            sum += (body17.charAt(i) - '0') * weights[i];
        }
        return "10X98765432".charAt(sum % 11);
    }

    /** 下游时间（诊断/处方）也必须落在 now 之前 */
    @Test
    void downstream_events_are_not_in_the_future() {
        DataGeneratorService svc = buildService();
        svc.generate(5);
        LocalDateTime now = LocalDateTime.now();

        for (Diagnosis d : insertedDiagnoses) {
            assertThat(d.getDiagnosedAt()).isBeforeOrEqualTo(now);
        }
        for (Prescription pr : insertedPrescriptions) {
            assertThat(pr.getPrescribedAt()).isBeforeOrEqualTo(now);
        }
    }

    /** 处方明细必须挂到已生成的处方上，且处方金额等于明细汇总（prescription_id 不可为 null）。 */
    @Test
    void prescription_items_reference_their_prescription_with_rolled_up_total() {
        DataGeneratorService svc = buildService();
        svc.generate(2);

        assertThat(insertedPrescriptions).isNotEmpty();
        assertThat(insertedItems).isNotEmpty();

        Map<Long, BigDecimal> priceById = Map.of(
                DRUG_AMOX, new BigDecimal("18.50"),
                DRUG_GANMAO, new BigDecimal("12.00"),
                DRUG_IBUPROFEN, new BigDecimal("15.80"));
        Map<Long, List<PrescriptionItem>> byRx = new HashMap<>();
        for (PrescriptionItem it : insertedItems) {
            assertThat(it.getPrescriptionId()).isNotNull();          // 必须引用真实处方 id
            byRx.computeIfAbsent(it.getPrescriptionId(), k -> new ArrayList<>()).add(it);
        }
        assertThat(byRx.keySet())
                .containsExactlyInAnyOrderElementsOf(insertedPrescriptions.stream().map(Prescription::getId).toList());

        for (Prescription pr : insertedPrescriptions) {
            List<PrescriptionItem> its = byRx.get(pr.getId());
            assertThat(its).isNotNull();
            BigDecimal expected = its.stream()
                    .map(it -> priceById.get(it.getDrugId()).multiply(it.getQuantity()))
                    .reduce(BigDecimal.ZERO, BigDecimal::add);
            assertThat(pr.getTotalAmount()).isEqualByComparingTo(expected);
        }
        // 总额是在插入之后回写的：一旦去掉 updateById，本断言即失败
        verify(prescriptionMapper, atLeastOnce()).updateById(anyList());
    }

    /** 跨越 BATCH_SIZE 分块边界时同样正确（分批写入路径） */
    @Test
    void generation_chunks_across_batch_boundary() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(501);

        assertThat(stat.get("patients")).isEqualTo(501L);
        assertThat(stat.get("visits")).isGreaterThanOrEqualTo(501L * 5);
        assertThat(insertedVisits).hasSize(stat.get("visits").intValue());
        for (Visit v : insertedVisits) {
            assertThat(v.getId()).isNotNull();
            assertThat(v.getPatientId()).isNotNull();
        }
        assertThat(insertedItems).isNotEmpty();
        assertThat(insertedItems).allSatisfy(it -> assertThat(it.getPrescriptionId()).isNotNull());
    }

    @Test
    void generates_charges_and_dispenses_per_visit() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(5);
        assertThat(stat.get("charges")).isGreaterThanOrEqualTo(10L);   // 每就诊至少挂号+药费两条
        assertThat(stat.get("dispenses")).isGreaterThanOrEqualTo(5L);
    }

    @Test
    void inpatient_patients_get_admissions_with_strictly_increasing_events() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(200);
        assertThat(stat.get("admissions")).isGreaterThan(0L);
        assertThat(stat.get("vital_signs")).isGreaterThan(0L);
        assertThat(stat.get("orders")).isGreaterThan(0L);

        Map<Long, Admission> admById = insertedAdmissions.stream()
                .collect(Collectors.toMap(Admission::getId, a -> a));
        for (MedicalOrder o : insertedOrders) {
            Admission a = admById.get(o.getAdmissionId());
            assertThat(a).isNotNull();
            assertThat(o.getStartTime()).isAfterOrEqualTo(a.getAdmissionTime());
        }
        for (VitalSign vs : insertedVitals) {
            Admission a = admById.get(vs.getAdmissionId());
            assertThat(a).isNotNull();
            assertThat(vs.getRecordedAt()).isAfterOrEqualTo(a.getAdmissionTime());
        }
        for (Admission a : insertedAdmissions) {
            assertThat(a.getDischargeTime()).isAfter(a.getAdmissionTime());
        }
        for (MedicalOrder o : insertedOrders) {
            assertThat(o.getStartTime()).isBefore(admById.get(o.getAdmissionId()).getDischargeTime());
        }
        for (VitalSign vs : insertedVitals) {
            assertThat(vs.getRecordedAt()).isBefore(admById.get(vs.getAdmissionId()).getDischargeTime());
        }

        // Finding 1：同一患者多次住院严格升序、互不重叠、入院时间不晚于 now。
        LocalDateTime now = LocalDateTime.now();
        Map<Long, List<Admission>> byPatient = insertedAdmissions.stream()
                .collect(Collectors.groupingBy(Admission::getPatientId));
        for (List<Admission> admissions : byPatient.values()) {
            admissions.sort(Comparator.comparing(Admission::getAdmissionTime));
            for (int i = 1; i < admissions.size(); i++) {
                Admission prev = admissions.get(i - 1);
                Admission cur = admissions.get(i);
                assertThat(cur.getAdmissionTime()).isAfter(prev.getAdmissionTime());   // 严格升序
                assertThat(cur.getAdmissionTime()).isAfter(prev.getDischargeTime());   // 互不重叠
            }
            for (Admission a : admissions) {
                assertThat(a.getAdmissionTime()).isBeforeOrEqualTo(now);               // 无未来入院
            }
        }
    }

    /**
     * 住院链的入院诊断也必须来自本体：延续该患者<b>自己的门诊诊断</b>，
     * 且该诊断属于<b>住院科室</b>的 dept_diagnosis；旧列 {@code diagnosis} 继续写且等于
     * {@code admission_diagnosis}。
     */
    @Test
    void admission_diagnosis_continues_an_outpatient_diagnosis_of_its_department() {
        DataGeneratorService svc = buildService();
        svc.generate(200);
        assertThat(insertedAdmissions).isNotEmpty();

        Map<Long, Set<String>> outpatientByPatient = new HashMap<>();
        for (Diagnosis d : insertedDiagnoses) {
            outpatientByPatient.computeIfAbsent(d.getPatientId(), k -> new HashSet<>())
                    .add(d.getIcdCode() + "|" + d.getDiagnosisName());
        }
        Map<String, String> icdByName = DX_NAME.entrySet().stream()
                .collect(Collectors.toMap(Map.Entry::getValue, Map.Entry::getKey));

        for (Admission a : insertedAdmissions) {
            assertThat(a.getDiagnosis()).isNotBlank();
            assertThat(a.getAdmissionDiagnosis()).isEqualTo(a.getDiagnosis());   // 旧列不留空且两列相等
            String icd = icdByName.get(a.getDiagnosis());
            assertThat(icd).as("入院诊断「%s」不是本体里的诊断", a.getDiagnosis()).isNotNull();
            assertThat(DX_DEPT.get(icd))
                    .as("入院诊断 %s 不属于住院科室 %s", icd, a.getDepartment())
                    .isEqualTo(a.getDepartment());
            assertThat(outpatientByPatient.get(a.getPatientId()))
                    .as("入院诊断未延续该患者的门诊诊断")
                    .contains(icd + "|" + a.getDiagnosis());
        }
    }

    /** 住院用药同样走 diagnosis_drug（药名必须落在住院诊断的用药池里） */
    @Test
    void inpatient_orders_use_the_ontology_drugs_of_the_admission_diagnosis() {
        DataGeneratorService svc = buildService();
        svc.generate(200);

        Map<Long, Admission> admById = insertedAdmissions.stream()
                .collect(Collectors.toMap(Admission::getId, a -> a));
        Map<String, String> icdByName = DX_NAME.entrySet().stream()
                .collect(Collectors.toMap(Map.Entry::getValue, Map.Entry::getKey));
        Set<String> ontologyDrugNames = Set.of("阿莫西林胶囊", "感冒灵颗粒", "布洛芬缓释胶囊");

        assertThat(insertedOrders).isNotEmpty();
        for (MedicalOrder o : insertedOrders) {
            Admission a = admById.get(o.getAdmissionId());
            String icd = icdByName.get(a.getDiagnosis());
            if (icd == null) {
                continue;   // 路径步骤里写的药名（clinical_pathway.steps），不由诊断池决定
            }
            if (!ontologyDrugNames.contains(o.getItemName())) {
                continue;   // 同上：路径 MED 步骤的 item
            }
            assertThat(DRUG_POOL.get(icd + "|" + a.getDepartment()))
                    .as("医嘱药 %s 不在 (%s|%s) 的用药池里", o.getItemName(), icd, a.getDepartment())
                    .isNotEmpty();
        }
        // 长期医嘱（orderType=0）的药名必须来自本体药名，不允许随机药
        List<MedicalOrder> longOrders = insertedOrders.stream().filter(o -> o.getOrderType() == 0).toList();
        assertThat(longOrders).isNotEmpty();
        assertThat(longOrders).allSatisfy(o -> assertThat(ontologyDrugNames).contains(o.getItemName()));
    }

    /** 幂等守卫：生成前清空十一张业务数据表，且不碰 drug / rule_config */
    @Test
    void clears_data_tables_before_generation() {
        DataGeneratorService svc = buildService();
        svc.generate(1);

        for (String table : List.of("patient", "visit", "diagnosis", "prescription", "prescription_item",
                "lab_test", "charge", "dispense", "admission", "medical_order", "vital_sign")) {
            verify(jdbcTemplate).execute("TRUNCATE TABLE " + table);
        }
        verify(jdbcTemplate, never()).execute(contains("drug"));
        verify(jdbcTemplate, never()).execute(contains("rule_config"));
    }

    @Test
    void clear_before_run_can_be_disabled() {
        DataGeneratorService svc = buildService();
        ReflectionTestUtils.setField(svc, "clearBeforeRun", false);
        svc.generate(1);

        verifyNoInteractions(jdbcTemplate);
    }

    /** 本体为空时必须快速失败并给出可读信息（而不是静默生成一堆没科室归属的诊断） */
    @Test
    void fails_fast_when_ontology_is_empty() {
        DataGeneratorService svc = buildService();
        when(deptDiagnosisMapper.selectList(any())).thenReturn(List.of());

        assertThatThrownBy(() -> svc.generate(1))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("dept_diagnosis");
        assertThat(insertedVisits).isEmpty();
    }

    /** 本体引用了不存在的 drug_id 时必须显式失败，而不是静默丢弃该药 */
    @Test
    void fails_loudly_when_ontology_references_a_missing_drug() {
        DataGeneratorService svc = buildService();
        when(diagnosisDrugMapper.selectList(any())).thenReturn(List.of(
                dxDrug(ICD_URI, "内科", 999L, 1, 1.0),
                dxDrug(ICD_HTN, "内科", 999L, 1, 1.0),
                dxDrug(ICD_WOUND, "外科", 999L, 1, 1.0)));

        assertThatThrownBy(() -> svc.generate(1))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("999");
    }

    @Test
    void visit_doctor_matches_its_department() {
        DataGeneratorService svc = buildService();
        svc.generate(50);

        Map<String, List<String>> deptDoctors = Map.of(
                "内科", List.of("李建华"),
                "外科", List.of("杨文博"));
        for (Visit v : insertedVisits) {
            assertThat(deptDoctors.get(v.getDepartment())).contains(v.getDoctor());
        }
    }

    @Test
    void patients_are_concentrated_in_a_home_department() {
        DataGeneratorService svc = buildService();
        svc.generate(200);

        // 汇总校验：所有患者「最常就诊科室」的就诊数合计，应显著高于均匀分布。
        // 均匀分布（2 科室）最常科室占比 ≈ 1/2；80/20 主科室占比 ≈ 0.8，二者差距大，
        // 汇总后小样本随机波动相互抵消，不会因个别低就诊数患者（如 5 次）而误报。
        long totalVisits = 0;
        long maxDeptVisits = 0;
        for (List<Visit> visits : visitsByPatient().values()) {
            Map<String, Long> deptCount = visits.stream()
                    .collect(Collectors.groupingBy(Visit::getDepartment, Collectors.counting()));
            long max = deptCount.values().stream().mapToLong(Long::longValue).max().orElse(0);
            totalVisits += visits.size();
            maxDeptVisits += max;
        }
        assertThat(maxDeptVisits).isGreaterThan(totalVisits * 60 / 100);
    }

    @Test
    void fails_fast_when_doctor_table_empty() {
        DataGeneratorService svc = buildService();
        when(doctorMapper.selectList(any())).thenReturn(List.of());

        assertThatThrownBy(() -> svc.generate(1))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("doctor");
    }
}

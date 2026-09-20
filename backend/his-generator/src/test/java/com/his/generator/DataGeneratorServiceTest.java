package com.his.generator;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.util.ReflectionTestUtils;

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

class DataGeneratorServiceTest {

    private static final long RULE_DRUG_1 = 1L;
    private static final long RULE_DRUG_2 = 2L;

    private final List<Patient> insertedPatients = new ArrayList<>();
    private final List<Visit> insertedVisits = new ArrayList<>();
    private final List<Diagnosis> insertedDiagnoses = new ArrayList<>();
    private final List<Prescription> insertedPrescriptions = new ArrayList<>();
    private final List<PrescriptionItem> insertedItems = new ArrayList<>();
    private final List<Admission> insertedAdmissions = new ArrayList<>();
    private final List<MedicalOrder> insertedOrders = new ArrayList<>();
    private final List<VitalSign> insertedVitals = new ArrayList<>();

    private RuleConfigMapper ruleConfigMapper;
    private DoctorMapper doctorMapper;
    private PrescriptionMapper prescriptionMapper;
    private RationalRuleMapper rationalRuleMapper;
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
        ruleConfigMapper = mock(RuleConfigMapper.class);
        doctorMapper = mock(DoctorMapper.class);
        jdbcTemplate = mock(JdbcTemplate.class);
        ChargeMapper chargeM = mock(ChargeMapper.class);
        DispenseMapper dispenseM = mock(DispenseMapper.class);
        rationalRuleMapper = mock(RationalRuleMapper.class);
        AdmissionMapper admissionMapper = mock(AdmissionMapper.class);
        MedicalOrderMapper medicalOrderMapper = mock(MedicalOrderMapper.class);
        VitalSignMapper vitalSignMapper = mock(VitalSignMapper.class);
        ClinicalPathwayMapper pathwayMapper = mock(ClinicalPathwayMapper.class);

        Doctor doc1 = new Doctor(); doc1.setId(1L); doc1.setName("李建华"); doc1.setDepartment("内科");
        Doctor doc2 = new Doctor(); doc2.setId(2L); doc2.setName("杨文博"); doc2.setDepartment("外科");
        when(doctorMapper.selectList(any())).thenReturn(List.of(doc1, doc2));

        Drug d1 = new Drug(); d1.setId(RULE_DRUG_1); d1.setName("感冒灵颗粒"); d1.setPrice(new BigDecimal("12.00")); d1.setUnit("盒");
        Drug d2 = new Drug(); d2.setId(RULE_DRUG_2); d2.setName("布洛芬"); d2.setPrice(new BigDecimal("15.80")); d2.setUnit("盒");
        Drug d3 = new Drug(); d3.setId(3L); d3.setName("硝苯地平"); d3.setPrice(new BigDecimal("28.00")); d3.setUnit("盒");
        when(drugM.selectList(any())).thenReturn(List.of(d1, d2, d3));

        RuleConfig r1 = new RuleConfig(); r1.setDiagnosisCode("J06.9"); r1.setDiagnosisName("上呼吸道感染"); r1.setDrugIds("1,2"); r1.setEnabled(1);
        when(ruleConfigMapper.selectList(any())).thenReturn(List.of(r1));

        // 默认无违规规则 → generate() 不注入违规，保证既有测试确定性
        when(rationalRuleMapper.selectList(any())).thenReturn(List.of());

        // 一条上呼吸道感染路径（steps JSON 与 Task 8 测试一致）
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
        stubBatchInsert(lm, null, LabTest::setId);
        stubBatchInsert(chargeM, null, Charge::setId);
        stubBatchInsert(dispenseM, null, Dispense::setId);
        stubBatchInsert(admissionMapper, insertedAdmissions, Admission::setId);
        stubBatchInsert(medicalOrderMapper, insertedOrders, MedicalOrder::setId);
        stubBatchInsert(vitalSignMapper, insertedVitals, VitalSign::setId);

        return new DataGeneratorService(pm, vm, dm, prescriptionMapper, im, lm, drugM, ruleConfigMapper,
                doctorMapper, jdbcTemplate, chargeM, dispenseM, rationalRuleMapper,
                admissionMapper, medicalOrderMapper, vitalSignMapper, pathwayMapper);
    }

    /** 构造一条仅设置药名/剂量/频次的处方明细（供违规注入测试） */
    private PrescriptionItem item(Long drugId, String dosage, String freq) {
        PrescriptionItem it = new PrescriptionItem();
        it.setDrugId(drugId);
        it.setDosage(dosage);
        it.setFrequency(freq);
        return it;
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

    @Test
    void diagnoses_come_from_rule_config() {
        DataGeneratorService svc = buildService();
        svc.generate(2);

        assertThat(insertedDiagnoses).isNotEmpty();
        for (Diagnosis d : insertedDiagnoses) {
            assertThat(d.getIcdCode()).isEqualTo("J06.9");
        }
    }

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

        Map<Long, BigDecimal> priceById = Map.of(RULE_DRUG_1, new BigDecimal("12.00"), RULE_DRUG_2, new BigDecimal("15.80"));
        Map<Long, List<PrescriptionItem>> itemsByPrescription = new HashMap<>();
        for (PrescriptionItem it : insertedItems) {
            assertThat(it.getPrescriptionId()).isNotNull();          // 必须引用真实处方 id
            itemsByPrescription.computeIfAbsent(it.getPrescriptionId(), k -> new ArrayList<>()).add(it);
        }
        assertThat(itemsByPrescription.keySet())
                .containsExactlyInAnyOrderElementsOf(insertedPrescriptions.stream().map(Prescription::getId).toList());

        for (Prescription pr : insertedPrescriptions) {
            List<PrescriptionItem> its = itemsByPrescription.get(pr.getId());
            assertThat(its).isNotNull();
            BigDecimal expected = its.stream()
                    .map(it -> priceById.get(it.getDrugId()).multiply(it.getQuantity()))
                    .reduce(BigDecimal.ZERO, BigDecimal::add);
            assertThat(pr.getTotalAmount()).isEqualByComparingTo(expected);
        }
        // 总额是在插入之后回写的：一旦去掉 updateById，本断言即失败
        verify(prescriptionMapper, atLeastOnce()).updateById(anyList());
    }

    /** 选用的药品必须来自该诊断的 rule_config.drug_ids */
    @Test
    void prescribed_drugs_are_subset_of_rule_drug_ids() {
        DataGeneratorService svc = buildService();
        svc.generate(5);

        assertThat(insertedItems).isNotEmpty();
        for (PrescriptionItem it : insertedItems) {
            assertThat(it.getDrugId()).isIn(RULE_DRUG_1, RULE_DRUG_2);
        }
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
    }

    @Test
    void injectViolation_produces_a_known_violation() {
        DataGeneratorService svc = buildService();
        RationalRule inter = new RationalRule();
        inter.setRuleType(1); inter.setDrugAId(RULE_DRUG_1); inter.setDrugBId(RULE_DRUG_2); inter.setEnabled(1);
        RationalRule dose = new RationalRule();
        dose.setRuleType(2); dose.setDrugId(RULE_DRUG_1); dose.setMaxDailyDose(new BigDecimal("8")); dose.setEnabled(1);
        when(rationalRuleMapper.selectList(any())).thenReturn(List.of(inter, dose));

        svc.generate(1);  // 触发加载 interactionRules / doseRulesByDrug

        MedicalData.DiagnosisRule rule = new MedicalData.DiagnosisRule("J06.9", "上呼吸道感染", List.of(RULE_DRUG_1, RULE_DRUG_2));
        List<PrescriptionItem> items = new ArrayList<>();
        items.add(item(RULE_DRUG_1, "1片", "每日1次"));
        String type = svc.injectViolation(items, rule);
        assertThat(type).isIn("DOSE", "INTERACTION", "INDICATION");
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

    /** rule_config 没有可用规则时快速失败，并给出可读信息（含 drug_ids 为 NULL 的行） */
    @Test
    void fails_fast_when_no_usable_rule_configured() {
        DataGeneratorService svc = buildService();
        RuleConfig nullDrugIds = new RuleConfig();
        nullDrugIds.setDiagnosisCode("R50.9"); nullDrugIds.setDiagnosisName("发热"); nullDrugIds.setDrugIds(null); nullDrugIds.setEnabled(1);
        when(ruleConfigMapper.selectList(any())).thenReturn(List.of(nullDrugIds));

        assertThatThrownBy(() -> svc.generate(1))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("rule_config");
        assertThat(insertedVisits).isEmpty();
    }

    /** 规则引用了不存在的 drug_id 时必须显式失败，而不是静默丢弃该药 */
    @Test
    void fails_loudly_when_rule_references_missing_drug() {
        DataGeneratorService svc = buildService();
        RuleConfig bad = new RuleConfig();
        bad.setDiagnosisCode("Z00.0"); bad.setDiagnosisName("体检"); bad.setDrugIds("999"); bad.setEnabled(1);
        when(ruleConfigMapper.selectList(any())).thenReturn(List.of(bad));

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
        // 均匀分布（6 科室）最常科室占比 ≈ 1/6；80/20 主科室占比 ≈ 0.8，二者差距大，
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

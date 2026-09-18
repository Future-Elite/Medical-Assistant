package com.his.generator;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BiConsumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.Mockito.*;

class DataGeneratorServiceTest {

    private static final long RULE_DRUG_1 = 1L;
    private static final long RULE_DRUG_2 = 2L;

    private final List<Visit> insertedVisits = new ArrayList<>();
    private final List<Diagnosis> insertedDiagnoses = new ArrayList<>();
    private final List<Prescription> insertedPrescriptions = new ArrayList<>();
    private final List<PrescriptionItem> insertedItems = new ArrayList<>();

    private RuleConfigMapper ruleConfigMapper;
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
        ruleConfigMapper = mock(RuleConfigMapper.class);
        jdbcTemplate = mock(JdbcTemplate.class);

        Drug d1 = new Drug(); d1.setId(RULE_DRUG_1); d1.setName("感冒灵颗粒"); d1.setPrice(new BigDecimal("12.00")); d1.setUnit("盒");
        Drug d2 = new Drug(); d2.setId(RULE_DRUG_2); d2.setName("布洛芬"); d2.setPrice(new BigDecimal("15.80")); d2.setUnit("盒");
        when(drugM.selectList(any())).thenReturn(List.of(d1, d2));

        RuleConfig r1 = new RuleConfig(); r1.setDiagnosisCode("J06.9"); r1.setDiagnosisName("上呼吸道感染"); r1.setDrugIds("1,2"); r1.setEnabled(1);
        when(ruleConfigMapper.selectList(any())).thenReturn(List.of(r1));

        stubBatchInsert(pm, null, Patient::setId);
        stubBatchInsert(vm, insertedVisits, Visit::setId);
        stubBatchInsert(dm, insertedDiagnoses, Diagnosis::setId);
        stubBatchInsert(prescriptionMapper, insertedPrescriptions, Prescription::setId);
        stubBatchInsert(im, insertedItems, PrescriptionItem::setId);
        stubBatchInsert(lm, null, LabTest::setId);

        return new DataGeneratorService(pm, vm, dm, prescriptionMapper, im, lm, drugM, ruleConfigMapper, jdbcTemplate);
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
            assertThat(first).isAfter(now.minusYears(3).minusDays(2));      // 不超过 3 年病史
            long spanDays = ChronoUnit.DAYS.between(first, last);
            assertThat(spanDays).isBetween(365L, 1096L);                    // 跨度 1-3 年
        }
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

    /** 幂等守卫：生成前清空六张业务数据表，且不碰 drug / rule_config */
    @Test
    void clears_data_tables_before_generation() {
        DataGeneratorService svc = buildService();
        svc.generate(1);

        for (String table : List.of("patient", "visit", "diagnosis", "prescription", "prescription_item", "lab_test")) {
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
}

package com.his.server.service.validation;

import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.RationalViolation;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class RationalDrugValidatorTest {

    private PrescriptionMapper pm;
    private PrescriptionItemMapper im;
    private DiagnosisMapper dm;
    private VisitMapper vm;
    private DiagnosisDrugMapper ddgm;
    private RationalRuleMapper rrm;
    private DrugDoseMapper ddm;

    @BeforeEach
    void setUp() {
        pm = mock(PrescriptionMapper.class);
        im = mock(PrescriptionItemMapper.class);
        dm = mock(DiagnosisMapper.class);
        vm = mock(VisitMapper.class);
        ddgm = mock(DiagnosisDrugMapper.class);
        rrm = mock(RationalRuleMapper.class);
        ddm = mock(DrugDoseMapper.class);
        lenient().when(im.selectList(any())).thenReturn(List.of());
        lenient().when(dm.selectList(any())).thenReturn(List.of());
        lenient().when(vm.selectList(any())).thenReturn(List.of());
        lenient().when(ddgm.selectList(any())).thenReturn(List.of());
        lenient().when(rrm.selectList(any())).thenReturn(List.of());
        lenient().when(ddm.selectList(any())).thenReturn(List.of());
    }

    private RationalDrugValidator validator() {
        return new RationalDrugValidator(pm, im, dm, vm, ddgm, rrm, ddm);
    }

    /** 造一条处方 + 一条明细 + 一条诊断 + 一次就诊（科室），减少各用例的样板。 */
    private void stub(Prescription p, PrescriptionItem item, String icdCode, String department) {
        when(pm.selectList(any())).thenReturn(List.of(p));
        when(im.selectList(any())).thenReturn(List.of(item));
        Diagnosis d = new Diagnosis();
        d.setVisitId(p.getVisitId());
        d.setIcdCode(icdCode);
        when(dm.selectList(any())).thenReturn(List.of(d));
        Visit v = new Visit();
        v.setId(p.getVisitId());
        v.setDepartment(department);
        when(vm.selectList(any())).thenReturn(List.of(v));
    }

    private Prescription prescription(long id, long visitId) {
        Prescription p = new Prescription();
        p.setId(id);
        p.setPatientId(1L);
        p.setVisitId(visitId);
        return p;
    }

    private PrescriptionItem item(long prescriptionId, long drugId, String dosage, String frequency) {
        PrescriptionItem it = new PrescriptionItem();
        it.setPrescriptionId(prescriptionId);
        it.setDrugId(drugId);
        it.setDrugName("药" + drugId);
        it.setDosage(dosage);
        it.setFrequency(frequency);
        return it;
    }

    /** department 传 null 表示「该诊断不限科室」的行。 */
    private DiagnosisDrug diagDrug(String icdCode, String department, long drugId) {
        DiagnosisDrug dd = new DiagnosisDrug();
        dd.setIcdCode(icdCode);
        dd.setDepartment(department);
        dd.setDrugId(drugId);
        dd.setLine(1);
        return dd;
    }

    private DrugDose drugDose(long drugId, String single, String maxDaily) {
        DrugDose d = new DrugDose();
        d.setDrugId(drugId);
        d.setSingleDose(new BigDecimal(single));
        d.setMaxDailyDose(new BigDecimal(maxDaily));
        d.setDoseUnit("片");
        d.setFrequency("每日1次");
        return d;
    }

    @Test
    void flags_indication_mismatch() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 99L, "1片", "每日1次"), "J06.9", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(
                diagDrug("J06.9", "内科", 1L), diagDrug("J06.9", "内科", 2L)));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).hasSize(1);
        assertThat(report.getViolations().get(0).getRuleType()).isEqualTo("INDICATION");
        assertThat(report.getViolationPrescriptions()).isEqualTo(1);
        assertThat(report.getRationalRate()).isEqualTo(0.0);
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void flags_dose_exceeding_limit() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 4L, "2片", "每日3次"), "I10", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(diagDrug("I10", "内科", 4L)));
        // 日剂量 2×3 = 6 片 > 上限 2 片
        when(ddm.selectList(any())).thenReturn(List.of(drugDose(4L, "1", "2")));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).extracting(RationalViolation::getRuleType).contains("DOSE");
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void clean_prescription_has_full_rational_rate() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 1L, "1片", "每日1次"), "J06.9", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(
                diagDrug("J06.9", "内科", 1L), diagDrug("J06.9", "内科", 2L)));
        when(ddm.selectList(any())).thenReturn(List.of(drugDose(1L, "1", "4")));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getRationalRate()).isEqualTo(1.0);
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void unchecked_counts_prescriptions_whose_diagnosis_is_absent_from_ontology() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 1L, "1片", "每日1次"), "Z99.9", "内科");
        // 本体里没有 Z99.9 -> 不能伪装成「通过」，必须计入 unchecked
        when(ddgm.selectList(any())).thenReturn(List.of(diagDrug("J06.9", "内科", 1L)));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getUnchecked()).isEqualTo(1);
    }

    @Test
    void unrecognized_frequency_counts_as_unchecked_not_a_violation() {
        Prescription p = prescription(1L, 10L);
        stub(p, item(1L, 4L, "2片", "bid"), "I10", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(diagDrug("I10", "内科", 4L)));
        when(ddm.selectList(any())).thenReturn(List.of(drugDose(4L, "1", "2")));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getUnchecked()).isEqualTo(1);
    }

    @Test
    void exact_department_row_wins_over_the_null_row() {
        Prescription p = prescription(1L, 10L);
        // 儿科开 J06.9：儿科行只允许药 1，不限科室的行允许药 1 与药 2。
        // 精确匹配必须赢——用 2 应当判 INDICATION，证明走的是儿科那一行。
        stub(p, item(1L, 2L, "1片", "每日1次"), "J06.9", "儿科");
        when(ddgm.selectList(any())).thenReturn(List.of(
                diagDrug("J06.9", "儿科", 1L),
                diagDrug("J06.9", null, 1L),
                diagDrug("J06.9", null, 2L)));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).extracting(RationalViolation::getRuleType).contains("INDICATION");
        assertThat(report.getUnchecked()).isZero();
    }

    @Test
    void null_department_row_applies_when_no_exact_row_exists() {
        Prescription p = prescription(1L, 10L);
        // 该诊断在本体里只有「不限科室」的行 -> 落回退分支，药 1 合规
        stub(p, item(1L, 1L, "1片", "每日1次"), "J06.9", "内科");
        when(ddgm.selectList(any())).thenReturn(List.of(diagDrug("J06.9", null, 1L)));
        when(ddm.selectList(any())).thenReturn(List.of(drugDose(1L, "1", "4")));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getUnchecked()).isZero();
        assertThat(report.getRationalRate()).isEqualTo(1.0);
    }

    @Test
    void diagnosis_not_covering_this_department_is_unchecked_not_a_drug_violation() {
        Prescription p = prescription(1L, 10L);
        // J06.9 在本体里只有内科/儿科的行，妇科开它 -> 用药校验无从判定
        stub(p, item(1L, 1L, "1片", "每日1次"), "J06.9", "妇科");
        when(ddgm.selectList(any())).thenReturn(List.of(
                diagDrug("J06.9", "内科", 1L), diagDrug("J06.9", "儿科", 1L)));

        RationalDrugReport report = validator().validate();

        // 职责划分：「妇科看 J06.9」本身违规，由 OntologyConsistencyValidator 的
        // DEPT_DIAGNOSIS 判，本校验器只如实报「未覆盖」，不在这里重复判、更不静默通过
        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getUnchecked()).isEqualTo(1);
    }

    @Test
    void firstDecimal_parses_the_leading_number() {
        assertThat(RationalDrugValidator.firstDecimal("1片")).isEqualByComparingTo("1");
        assertThat(RationalDrugValidator.firstDecimal("0.5片")).isEqualByComparingTo("0.5");
        assertThat(RationalDrugValidator.firstDecimal("10ml")).isEqualByComparingTo("10");
        assertThat(RationalDrugValidator.firstDecimal("每日3次")).isNull();
        assertThat(RationalDrugValidator.firstDecimal(null)).isNull();
        // 小数点单独出现在首尾都不构成数字（".5片" 无从判定；"1." 不能喂给 BigDecimal，它会抛）
        assertThat(RationalDrugValidator.firstDecimal(".5片")).isNull();
        assertThat(RationalDrugValidator.firstDecimal("1.")).isEqualByComparingTo("1");
    }
}

package com.his.server.service.validation;

import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.RationalViolation;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class RationalDrugValidatorTest {

    private PrescriptionMapper pm;
    private PrescriptionItemMapper im;
    private DiagnosisMapper dm;
    private RuleConfigMapper rcm;
    private RationalRuleMapper rrm;

    @BeforeEach
    void setUp() {
        pm = mock(PrescriptionMapper.class);
        im = mock(PrescriptionItemMapper.class);
        dm = mock(DiagnosisMapper.class);
        rcm = mock(RuleConfigMapper.class);
        rrm = mock(RationalRuleMapper.class);
    }

    private RationalDrugValidator validator() {
        return new RationalDrugValidator(pm, im, dm, rcm, rrm);
    }

    @Test
    void flags_indication_mismatch() {
        Prescription p = new Prescription(); p.setId(1L); p.setPatientId(1L); p.setVisitId(10L);
        when(pm.selectList(any())).thenReturn(List.of(p));

        PrescriptionItem item = new PrescriptionItem();
        item.setPrescriptionId(1L); item.setDrugId(99L); item.setDosage("1片"); item.setFrequency("每日1次");
        when(im.selectList(any())).thenReturn(List.of(item));

        Diagnosis d = new Diagnosis(); d.setVisitId(10L); d.setIcdCode("J06.9");
        when(dm.selectList(any())).thenReturn(List.of(d));

        RuleConfig rc = new RuleConfig(); rc.setDiagnosisCode("J06.9"); rc.setDrugIds("1,2"); rc.setEnabled(1);
        when(rcm.selectList(any())).thenReturn(List.of(rc));
        when(rrm.selectList(any())).thenReturn(List.of());

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).hasSize(1);
        assertThat(report.getViolations().get(0).getRuleType()).isEqualTo("INDICATION");
        assertThat(report.getViolationPrescriptions()).isEqualTo(1);
        assertThat(report.getRationalRate()).isEqualTo(0.0);
    }

    @Test
    void flags_dose_exceeding_limit() {
        Prescription p = new Prescription(); p.setId(1L); p.setPatientId(1L); p.setVisitId(10L);
        when(pm.selectList(any())).thenReturn(List.of(p));

        PrescriptionItem item = new PrescriptionItem();
        item.setPrescriptionId(1L); item.setDrugId(4L); item.setDosage("4片"); item.setFrequency("每日3次");
        when(im.selectList(any())).thenReturn(List.of(item));

        Diagnosis d = new Diagnosis(); d.setVisitId(10L); d.setIcdCode("I10");
        when(dm.selectList(any())).thenReturn(List.of(d));

        RuleConfig rc = new RuleConfig(); rc.setDiagnosisCode("I10"); rc.setDrugIds("4"); rc.setEnabled(1);
        when(rcm.selectList(any())).thenReturn(List.of(rc));

        RationalRule dose = new RationalRule();
        dose.setRuleType(2); dose.setDrugId(4L); dose.setMaxDailyDose(new BigDecimal("8")); dose.setWarningLevel(1); dose.setMessage("剂量超标");
        when(rrm.selectList(any())).thenReturn(List.of(dose));

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).extracting(RationalViolation::getRuleType).contains("DOSE");
    }

    @Test
    void clean_prescription_has_full_rational_rate() {
        Prescription p = new Prescription(); p.setId(1L); p.setPatientId(1L); p.setVisitId(10L);
        when(pm.selectList(any())).thenReturn(List.of(p));

        PrescriptionItem item = new PrescriptionItem();
        item.setPrescriptionId(1L); item.setDrugId(1L); item.setDosage("1片"); item.setFrequency("每日1次");
        when(im.selectList(any())).thenReturn(List.of(item));

        Diagnosis d = new Diagnosis(); d.setVisitId(10L); d.setIcdCode("J06.9");
        when(dm.selectList(any())).thenReturn(List.of(d));

        RuleConfig rc = new RuleConfig(); rc.setDiagnosisCode("J06.9"); rc.setDrugIds("1,2"); rc.setEnabled(1);
        when(rcm.selectList(any())).thenReturn(List.of(rc));
        when(rrm.selectList(any())).thenReturn(List.of());

        RationalDrugReport report = validator().validate();

        assertThat(report.getViolations()).isEmpty();
        assertThat(report.getRationalRate()).isEqualTo(1.0);
    }
}

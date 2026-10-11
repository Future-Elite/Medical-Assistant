package com.his.server.service.validation;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.PathwayReport;
import com.his.common.dto.PathwayVariance;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class PathwayValidatorTest {

    private ClinicalPathwayMapper pm;
    private AdmissionMapper am;
    private MedicalOrderMapper om;
    private LabTestMapper lm;

    @BeforeEach
    void setUp() {
        pm = mock(ClinicalPathwayMapper.class);
        am = mock(AdmissionMapper.class);
        om = mock(MedicalOrderMapper.class);
        lm = mock(LabTestMapper.class);
    }

    private PathwayValidator validator() {
        return new PathwayValidator(pm, am, om, lm, new ObjectMapper());
    }

    private ClinicalPathway pathway() {
        ClinicalPathway p = new ClinicalPathway();
        p.setId(1L); p.setDiagnosisName("上呼吸道感染");
        p.setSteps("[{\"day\":0,\"action\":\"LAB\",\"item\":\"白细胞计数\",\"window\":24}," +
                "{\"day\":3,\"action\":\"DISCHARGE\",\"window\":24}]");
        p.setEnabled(1);
        return p;
    }

    @Test
    void compliant_admission_fulfills_all_steps() {
        when(pm.selectList(any())).thenReturn(List.of(pathway()));
        Admission a = new Admission();
        a.setId(1L); a.setPatientId(1L); a.setDiagnosis("上呼吸道感染");
        a.setAdmissionTime(LocalDateTime.of(2026, 1, 1, 8, 0));
        a.setDischargeTime(LocalDateTime.of(2026, 1, 4, 9, 0));
        when(am.selectList(any())).thenReturn(List.of(a));

        LabTest lab = new LabTest();
        lab.setAdmissionId(1L); lab.setItemName("白细胞计数");
        lab.setTestedAt(LocalDateTime.of(2026, 1, 1, 10, 0));
        when(lm.selectList(any())).thenReturn(List.of(lab));
        when(om.selectList(any())).thenReturn(List.of());

        PathwayReport report = validator().validate();

        assertThat(report.getEnrolledAdmissions()).isEqualTo(1);
        assertThat(report.getFulfilledSteps()).isEqualTo(2);
        assertThat(report.getVariances()).isEmpty();
        assertThat(report.getExecutionRate()).isEqualTo(1.0);
    }

    @Test
    void missing_step_produces_variance() {
        when(pm.selectList(any())).thenReturn(List.of(pathway()));
        Admission a = new Admission();
        a.setId(1L); a.setPatientId(1L); a.setDiagnosis("上呼吸道感染");
        a.setAdmissionTime(LocalDateTime.of(2026, 1, 1, 8, 0));
        // 未出院 → DISCHARGE 步骤缺失
        when(am.selectList(any())).thenReturn(List.of(a));
        when(lm.selectList(any())).thenReturn(List.of());
        when(om.selectList(any())).thenReturn(List.of());

        PathwayReport report = validator().validate();

        assertThat(report.getVariances()).hasSize(2);
        assertThat(report.getVariances()).extracting(PathwayVariance::getVarianceType)
                .containsOnly("MISSING");
    }
}

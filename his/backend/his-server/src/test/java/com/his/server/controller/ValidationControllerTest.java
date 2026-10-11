package com.his.server.controller;

import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.PathwayReport;
import com.his.common.dto.ValidationReport;
import com.his.server.service.ValidationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(ValidationController.class)
class ValidationControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean ValidationService validationService;

    @Test
    void run_returns_combined_report() throws Exception {
        ValidationReport report = new ValidationReport();
        RationalDrugReport rd = new RationalDrugReport();
        rd.setTotalPrescriptions(100); rd.setRationalRate(0.95);
        PathwayReport pw = new PathwayReport();
        pw.setEnrolledAdmissions(10); pw.setExecutionRate(0.8);
        report.setRationalDrug(rd); report.setPathway(pw);
        when(validationService.run()).thenReturn(report);

        mockMvc.perform(post("/api/validation/run"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.rationalDrug.rationalRate").value(0.95))
                .andExpect(jsonPath("$.data.pathway.enrolledAdmissions").value(10));
    }
}

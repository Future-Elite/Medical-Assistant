package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.his.common.entity.Admission;
import com.his.server.service.AdmissionService;
import com.his.server.service.TimelineService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(AdmissionController.class)
class AdmissionControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean AdmissionService admissionService;
    @MockBean TimelineService timelineService;

    @Test
    void list_returns_admissions_for_patient() throws Exception {
        Admission a = new Admission();
        a.setId(1L); a.setPatientId(10L); a.setAdmissionNo("A10-1");
        when(admissionService.list(any(Wrapper.class))).thenReturn(List.of(a));

        mockMvc.perform(get("/api/admissions").param("patientId", "10"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].admissionNo").value("A10-1"));
    }
}

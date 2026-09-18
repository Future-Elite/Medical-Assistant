package com.his.server.controller;

import com.his.common.entity.Patient;
import com.his.server.service.PatientService;
import com.his.server.service.TimelineService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(PatientController.class)
class PatientControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean PatientService patientService;
    @MockBean TimelineService timelineService;

    @Test
    void create_returnsPatient() throws Exception {
        Patient p = new Patient();
        p.setId(1L); p.setName("张三");
        when(patientService.save(any())).thenReturn(true);
        mockMvc.perform(post("/api/patients")
                        .contentType("application/json")
                        .content("{\"name\":\"张三\",\"gender\":0}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }

    @Test
    void getById_returnsPatient() throws Exception {
        Patient p = new Patient();
        p.setId(1L); p.setName("张三");
        when(patientService.getById(1L)).thenReturn(p);
        mockMvc.perform(get("/api/patients/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.name").value("张三"));
    }
}

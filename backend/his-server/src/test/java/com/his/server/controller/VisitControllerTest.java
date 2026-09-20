package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.his.common.entity.Visit;
import com.his.server.service.VisitService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(VisitController.class)
class VisitControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean VisitService visitService;

    @Test
    void list_returns_visits_for_patient() throws Exception {
        Visit v = new Visit();
        v.setId(1L); v.setPatientId(10L); v.setVisitNo("V10-1");
        v.setDepartment("内科"); v.setVisitTime(LocalDateTime.of(2026, 9, 20, 9, 0));
        when(visitService.list(any(Wrapper.class))).thenReturn(List.of(v));

        mockMvc.perform(get("/api/visits")
                        .param("patientId", "10")
                        .param("department", "内科"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].visitNo").value("V10-1"));
    }
}

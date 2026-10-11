package com.his.server.controller;

import com.his.common.entity.VitalSign;
import com.his.server.service.VitalSignService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(VitalSignController.class)
class VitalSignControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean VitalSignService vitalSignService;

    @Test
    void create_returnsVitalSign() throws Exception {
        when(vitalSignService.save(any())).thenReturn(true);
        mockMvc.perform(post("/api/vital-signs")
                        .contentType("application/json")
                        .content("{\"patientId\":1,\"admissionId\":2,\"temperature\":36.8,\"pulse\":78}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }
}

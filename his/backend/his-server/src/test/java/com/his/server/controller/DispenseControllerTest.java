package com.his.server.controller;

import com.his.common.entity.Dispense;
import com.his.server.service.DispenseService;
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

@WebMvcTest(DispenseController.class)
class DispenseControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean DispenseService dispenseService;

    @Test
    void create_returnsDispense() throws Exception {
        when(dispenseService.save(any())).thenReturn(true);
        mockMvc.perform(post("/api/dispenses")
                        .contentType("application/json")
                        .content("{\"patientId\":1,\"visitId\":2,\"prescriptionId\":3,\"drugName\":\"阿莫西林\",\"quantity\":2}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }
}

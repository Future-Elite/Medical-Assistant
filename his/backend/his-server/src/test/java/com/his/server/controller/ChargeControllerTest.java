package com.his.server.controller;

import com.his.common.entity.Charge;
import com.his.server.service.ChargeService;
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

@WebMvcTest(ChargeController.class)
class ChargeControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean ChargeService chargeService;

    @Test
    void create_returnsCharge() throws Exception {
        when(chargeService.save(any())).thenReturn(true);
        mockMvc.perform(post("/api/charges")
                        .contentType("application/json")
                        .content("{\"patientId\":1,\"visitId\":2,\"amount\":18.50}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }
}

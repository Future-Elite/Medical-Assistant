package com.his.server.controller;

import com.his.common.entity.MedicalOrder;
import com.his.server.service.MedicalOrderService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(MedicalOrderController.class)
class MedicalOrderControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean MedicalOrderService medicalOrderService;

    @Test
    void create_returnsOrder() throws Exception {
        when(medicalOrderService.save(any())).thenReturn(true);
        mockMvc.perform(post("/api/orders")
                        .contentType("application/json")
                        .content("{\"patientId\":1,\"admissionId\":2,\"itemName\":\"硝苯地平控释片\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }

    @Test
    void stop_delegates() throws Exception {
        MedicalOrder o = new MedicalOrder();
        o.setId(1L); o.setPatientId(1L); o.setStatus(1);
        when(medicalOrderService.stop(1L)).thenReturn(o);
        mockMvc.perform(put("/api/orders/1/stop"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value(1));
    }
}

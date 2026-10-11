package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.his.common.entity.Doctor;
import com.his.server.service.DoctorService;
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

@WebMvcTest(DoctorController.class)
class DoctorControllerTest {

    @Autowired MockMvc mockMvc;
    @MockBean DoctorService doctorService;

    @Test
    void list_returns_doctors() throws Exception {
        Doctor d = new Doctor();
        d.setId(1L); d.setName("李建华"); d.setDepartment("内科");
        when(doctorService.list(any(Wrapper.class))).thenReturn(List.of(d));

        mockMvc.perform(get("/api/doctors"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].name").value("李建华"))
                .andExpect(jsonPath("$.data[0].department").value("内科"));
    }
}

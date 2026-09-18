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

    /**
     * 重要 #4：任何未预期异常都必须以 {@code {code,message,data}} 信封返回，
     * 而不是 Spring 默认的 {@code {timestamp,status,error,path}}；
     * 且 message 不得泄露异常明细。
     */
    @Test
    void internal_error_is_wrapped_in_the_result_envelope() throws Exception {
        when(patientService.getById(1L)).thenThrow(new IllegalStateException("数据库连接串泄露风险文本"));

        mockMvc.perform(get("/api/patients/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(500))
                .andExpect(jsonPath("$.message").value("服务器内部错误"))
                .andExpect(jsonPath("$.timestamp").doesNotExist())
                .andExpect(jsonPath("$.status").doesNotExist())
                .andExpect(jsonPath("$.path").doesNotExist());
    }
}

package com.his.server.neonatal;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MockMvc;
import java.util.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(NeonatalController.class)
class NeonatalControllerTest {
    @Autowired MockMvc mvc;
    @MockBean NeonatalService service;
    @Test void returnsEnvelopeAndPassesCohortFilters() throws Exception {
        when(service.records(anyMap())).thenReturn(Map.of("total",1273,"records",List.of()));
        mvc.perform(get("/api/neonatal/records").param("gestation","unknown").param("batchId","1"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.code").value(0)).andExpect(jsonPath("$.data.total").value(1273));
        verify(service).records(argThat(f -> f.get("gestation").equals("unknown") && f.get("batchId").equals("1")));
    }
    @Test void reportsInvalidInputAsBusinessError() throws Exception {
        when(service.records(anyMap())).thenThrow(new IllegalArgumentException("page 参数超出范围"));
        mvc.perform(get("/api/neonatal/records").param("page","0"))
                .andExpect(jsonPath("$.code").value(400)).andExpect(jsonPath("$.message").value("page 参数超出范围"));
    }
    @Test void uploadUsesOriginalFileAndReturnsIdempotencyResult() throws Exception {
        byte[] bytes={1,2,3};
        when(service.importWorkbook(eq("source.xlsx"),any(byte[].class))).thenReturn(Map.of("id",1,"reused",true));
        mvc.perform(multipart("/api/neonatal/imports").file(new MockMultipartFile("file","source.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",bytes)))
                .andExpect(jsonPath("$.code").value(0)).andExpect(jsonPath("$.data.reused").value(true));
        verify(service).importWorkbook("source.xlsx",bytes);
    }
}

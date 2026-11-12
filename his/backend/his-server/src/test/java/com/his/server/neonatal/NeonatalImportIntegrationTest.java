package com.his.server.neonatal;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Opt-in MySQL checks. Successful test writes are rolled back; failure test verifies atomic rollback. */
@SpringBootTest(properties = "neonatal.import-on-start=false")
@EnabledIfSystemProperty(named="neonatal.integration", matches="true")
class NeonatalImportIntegrationTest {
    @Autowired NeonatalService service;
    @Autowired JdbcTemplate jdbc;

    @Test @Transactional void roundTripIdempotencyFiltersAndStatistics() throws Exception {
        String longText = "检验原文；".repeat(6500);
        var row = new LinkedHashMap<>(Map.of("性别", "女", "年龄", "7天", "住院号", "INTEGRATION-ONLY",
                "孕周", "35", "入院时间", "2024.01.02 10:30", "出院时间", "2024.01.08",
                "入院诊断", "新生儿肺炎？", "出院诊断", "新生儿肺炎新生儿高胆红素血症", "治疗结果", "治愈"));
        row.put("实验室检查及主要会诊", longText);
        byte[] file = NeonatalWorkbookReaderTest.workbook(List.of(row, row));
        var batch = service.importWorkbook("integration-test.xlsx", file);
        String batchId = batch.get("id").toString();
        assertEquals(2, ((Number)batch.get("recordCount")).intValue());
        assertEquals(true, service.importWorkbook("renamed.xlsx", file).get("reused"));
        var page = service.records(Map.of("batchId",batchId,"gestation","preterm","keyword","肺炎","gender","女"));
        assertEquals(2L, page.get("total"));
        var list = (List<Map<String,Object>>) page.get("records");
        var detail = service.detail(((Number)list.getFirst().get("id")).longValue());
        var raw = (Map<?,?>)detail.get("rawData");
        assertEquals(longText, raw.get("实验室检查及主要会诊"));
        assertEquals(longText, ((Map<?,?>)detail.get("summary")).get("laboratoryFindings"));
        assertNull(detail.get("gestationalDays"));
        assertEquals(6, ((Number)detail.get("lengthOfStay")).intValue());
        assertEquals(2L, service.records(Map.of("batchId",batchId,"issueCode","REPEATED_HOSPITAL_NO")).get("total"));
        assertEquals(0L, service.records(Map.of("batchId",batchId,"dateFrom","2024-01-03")).get("total"));
        var overview=(Map<?,?>)service.stats(Map.of("batchId",batchId)).get("overview");
        assertEquals(2L, ((Number)overview.get("records")).longValue());
        assertEquals(2L, ((Number)overview.get("preterm")).longValue());
        assertThrows(IllegalArgumentException.class,()->service.records(Map.of("page","0")));
    }
    @Test void databaseFailureRollsBackEntireBatch() throws Exception {
        long before=jdbc.queryForObject("SELECT COUNT(*) FROM neonatal_import_batch", Long.class);
        byte[] bytes=NeonatalWorkbookReaderTest.workbook(List.of(Map.of("住院号","ROLLBACK-TEST"),Map.of("住院号","X".repeat(101))));
        assertThrows(RuntimeException.class,()->service.importWorkbook("rollback-test.xlsx",bytes));
        assertEquals(before,jdbc.queryForObject("SELECT COUNT(*) FROM neonatal_import_batch",Long.class));
        assertEquals(0L,jdbc.queryForObject("SELECT COUNT(*) FROM neonatal_record WHERE hospital_no='ROLLBACK-TEST'",Long.class));
    }
}

package com.his.server.neonatal;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.*;
import org.springframework.jdbc.support.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.file.Path;
import java.security.MessageDigest;
import java.sql.*;
import java.time.LocalDate;
import java.util.*;

@Service
public class NeonatalService {
    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate named;
    private final ObjectMapper json;
    public NeonatalService(JdbcTemplate jdbc, ObjectMapper json) {
        this.jdbc = jdbc; this.named = new NamedParameterJdbcTemplate(jdbc); this.json = json;
    }

    private long insert(String sql, Object... args) {
        KeyHolder key = new GeneratedKeyHolder();
        jdbc.update(c -> {
            PreparedStatement p = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS);
            for (int i = 0; i < args.length; i++) p.setObject(i + 1, args[i]);
            return p;
        }, key);
        return Objects.requireNonNull(key.getKey()).longValue();
    }

    private String serialize(Object value) {
        try { return json.writeValueAsString(value); }
        catch (JsonProcessingException e) { throw new IllegalArgumentException("无法保存原始数据", e); }
    }

    @Transactional
    public synchronized Map<String, Object> importWorkbook(String filename, byte[] bytes) {
        if (filename == null || !filename.toLowerCase(Locale.ROOT).endsWith(".xlsx"))
            throw new IllegalArgumentException("请上传 .xlsx 格式的出院小结汇总");
        if (bytes.length == 0 || bytes.length > 50 * 1024 * 1024) throw new IllegalArgumentException("文件大小须在 0–50 MB 之间");
        String sha;
        try { sha = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (Exception e) { throw new IllegalStateException(e); }
        var existing = jdbc.queryForList("SELECT id FROM neonatal_import_batch WHERE file_sha256=?", sha);
        if (!existing.isEmpty()) return importResult(((Number) existing.getFirst().get("id")).longValue(), true);
        var workbook = new NeonatalWorkbookReader().read(bytes);
        String safeName = Path.of(filename.replace('\\', '/')).getFileName().toString();
        if (safeName.length() > 255) throw new IllegalArgumentException("文件名过长");
        long batchId = insert("INSERT INTO neonatal_import_batch(file_name,file_sha256,sheet_name) VALUES(?,?,?)",
                safeName, sha, workbook.sheetName());
        Map<String, Integer> frequencies = new HashMap<>();
        workbook.rows().forEach(r -> frequencies.merge(r.values().getOrDefault("住院号", "").strip(), 1, Integer::sum));
        int issueCount = 0;
        for (var row : workbook.rows()) {
            var v = row.values();
            var n = NeonatalNormalizer.normalize(v, frequencies.getOrDefault(v.getOrDefault("住院号", "").strip(), 0) > 1);
            long source = insert("INSERT INTO neonatal_source_row(batch_id,source_row_number,source_file,raw_data) VALUES(?,?,?,?)",
                    batchId, row.number(), v.get("来源文件"), serialize(v));
            long id = insert("""
                    INSERT INTO neonatal_record(batch_id,source_row_id,hospital_no,gender,reported_age,reported_age_days,
                    gestational_age_raw,gestational_weeks,gestational_days,admission_time,discharge_time,admission_time_raw,
                    discharge_time_raw,length_of_stay,treatment_result,outcome_group,issue_count)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, batchId, source, n.hospitalNo(), n.gender(), v.get("年龄"), n.ageDays(), v.get("孕周"), n.weeks(), n.days(),
                    n.admission(), n.discharge(), v.get("入院时间"), v.get("出院时间"), n.stay(), v.get("治疗结果"), n.outcome(), n.issues().size());
            jdbc.update("""
                    INSERT INTO neonatal_summary(record_id,presenting_symptoms,laboratory_findings,special_examinations,
                    treatment_course,complications,discharge_condition,discharge_orders,discharge_guidance)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """, id, v.get("入院时主要症状及体征"), v.get("实验室检查及主要会诊"), v.get("特殊检验及重要会诊"),
                    v.get("诊疗过程"), v.get("合并症"), v.get("出院时情况"), v.get("出院医嘱"), v.get("出院指导"));
            for (String phase : List.of("ADMISSION", "DISCHARGE"))
                jdbc.update("INSERT INTO neonatal_diagnosis(record_id,phase,diagnosis_text) VALUES(?,?,?)",
                        id, phase, v.getOrDefault(phase.equals("ADMISSION") ? "入院诊断" : "出院诊断", ""));
            for (var issue : n.issues()) jdbc.update("INSERT INTO neonatal_quality_issue(record_id,issue_code,field_name,message) VALUES(?,?,?,?)",
                    id, issue.code(), issue.field(), issue.message());
            issueCount += n.issues().size();
        }
        jdbc.update("UPDATE neonatal_import_batch SET record_count=?,issue_count=? WHERE id=?", workbook.rows().size(), issueCount, batchId);
        return importResult(batchId, false);
    }

    private Map<String, Object> importResult(long batchId, boolean reused) {
        Map<String, Object> result = new LinkedHashMap<>(batch(batchId));
        result.put("reused", reused);
        return result;
    }

    public List<Map<String, Object>> imports() {
        return jdbc.queryForList("""
                SELECT id,file_name AS fileName,sheet_name AS sheetName,record_count AS recordCount,
                issue_count AS issueCount,DATE_FORMAT(imported_at,'%Y-%m-%d %H:%i:%s') AS importedAt
                FROM neonatal_import_batch ORDER BY id DESC
                """);
    }

    public Map<String, Object> batch(Long id) {
        if (id == null || id == 0) return Map.of();
        var rows = jdbc.queryForList("""
                SELECT id,file_name AS fileName,file_sha256 AS fileSha256,sheet_name AS sheetName,
                record_count AS recordCount,issue_count AS issueCount,
                DATE_FORMAT(imported_at,'%Y-%m-%d %H:%i:%s') AS importedAt FROM neonatal_import_batch WHERE id=?
                """, id);
        if (rows.isEmpty()) throw new IllegalArgumentException("导入批次不存在");
        return rows.getFirst();
    }

    public long selectedBatch(Map<String, String> filters) {
        if (filters.containsKey("batchId") && !filters.get("batchId").isBlank()) {
            long id = number(filters, "batchId", 0, 1, Long.MAX_VALUE);
            batch(id); return id;
        }
        Long id = jdbc.queryForObject("SELECT MAX(id) FROM neonatal_import_batch", Long.class);
        return id == null ? 0 : id;
    }

    private static long number(Map<String, String> values, String name, long fallback, long min, long max) {
        String raw = values.get(name);
        if (raw == null || raw.isBlank()) return fallback;
        try { long n = Long.parseLong(raw); if (n >= min && n <= max) return n; }
        catch (NumberFormatException ignored) { }
        throw new IllegalArgumentException(name + " 参数超出范围");
    }

    private static final String JOINS = """
            FROM neonatal_record r JOIN neonatal_source_row s ON s.id=r.source_row_id
            JOIN neonatal_diagnosis da ON da.record_id=r.id AND da.phase='ADMISSION'
            JOIN neonatal_diagnosis dd ON dd.record_id=r.id AND dd.phase='DISCHARGE'
            """;
    private static final String COLUMNS = """
            r.id,r.batch_id AS batchId,r.hospital_no AS hospitalNo,r.gender,r.reported_age AS reportedAge,
            r.reported_age_days AS reportedAgeDays,r.gestational_age_raw AS gestationalAgeRaw,
            r.gestational_weeks AS gestationalWeeks,r.gestational_days AS gestationalDays,
            DATE_FORMAT(r.admission_time,'%Y-%m-%d %H:%i') AS admissionTime,
            DATE_FORMAT(r.discharge_time,'%Y-%m-%d %H:%i') AS dischargeTime,
            r.admission_time_raw AS admissionTimeRaw,r.discharge_time_raw AS dischargeTimeRaw,
            r.length_of_stay AS lengthOfStay,r.treatment_result AS treatmentResult,r.outcome_group AS outcomeGroup,
            r.issue_count AS issueCount,s.source_row_number AS rowNumber,s.source_file AS sourceFile,
            da.diagnosis_text AS admissionDiagnosis,dd.diagnosis_text AS dischargeDiagnosis
            """;

    private record Filter(String sql, MapSqlParameterSource params) { }
    private Filter filter(Map<String, String> values) {
        StringBuilder sql = new StringBuilder(" WHERE r.batch_id=:batch");
        MapSqlParameterSource p = new MapSqlParameterSource("batch", selectedBatch(values));
        String keyword = values.getOrDefault("keyword", "").strip();
        if (keyword.length() > 200) throw new IllegalArgumentException("搜索词最多 200 字");
        if (!keyword.isEmpty()) {
            sql.append(" AND (r.hospital_no LIKE :keyword OR da.diagnosis_text LIKE :keyword OR dd.diagnosis_text LIKE :keyword)");
            p.addValue("keyword", "%" + keyword + "%");
        }
        for (String key : List.of("gender", "outcome")) if (!values.getOrDefault(key, "").isBlank()) {
            sql.append(key.equals("gender") ? " AND r.gender=:gender" : " AND r.outcome_group=:outcome");
            p.addValue(key, values.get(key));
        }
        String ga = values.getOrDefault("gestation", "");
        switch (ga) {
            case "" -> { }
            case "unknown" -> sql.append(" AND r.gestational_weeks IS NULL");
            case "preterm" -> sql.append(" AND r.gestational_weeks < 37");
            case "term" -> sql.append(" AND r.gestational_weeks >=37 AND r.gestational_weeks <42");
            case "postterm" -> sql.append(" AND r.gestational_weeks >=42");
            default -> throw new IllegalArgumentException("孕周分组无效");
        }
        for (String key : List.of("dateFrom", "dateTo")) if (!values.getOrDefault(key, "").isBlank()) {
            try {
                LocalDate date = LocalDate.parse(values.get(key));
                sql.append(key.equals("dateFrom") ? " AND r.admission_time>=:dateFrom" : " AND r.admission_time<:dateTo");
                p.addValue(key, Timestamp.valueOf((key.equals("dateTo") ? date.plusDays(1) : date).atStartOfDay()));
            } catch (RuntimeException e) { throw new IllegalArgumentException("入院日期筛选格式无效"); }
        }
        if ("true".equals(values.get("issuesOnly"))) sql.append(" AND r.issue_count>0");
        if (!values.getOrDefault("issueCode", "").isBlank()) {
            sql.append(" AND EXISTS (SELECT 1 FROM neonatal_quality_issue q WHERE q.record_id=r.id AND q.issue_code=:issueCode)");
            p.addValue("issueCode", values.get("issueCode"));
        }
        return new Filter(sql.toString(), p);
    }

    public Map<String, Object> records(Map<String, String> values) {
        long page = number(values, "page", 1, 1, 1000000), size = number(values, "size", 20, 1, 100);
        Filter f = filter(values);
        Long count = named.queryForObject("SELECT COUNT(*) " + JOINS + f.sql(), f.params(), Long.class);
        f.params().addValue("size", size).addValue("offset", (page - 1) * size);
        var records = named.queryForList("SELECT " + COLUMNS + JOINS + f.sql()
                + " ORDER BY r.admission_time DESC,r.id DESC LIMIT :size OFFSET :offset", f.params());
        return Map.of("records", records, "total", Objects.requireNonNull(count), "page", page, "size", size);
    }

    public Map<String, Object> detail(long id) {
        var rows = jdbc.queryForList("SELECT " + COLUMNS + JOINS + " WHERE r.id=?", id);
        if (rows.isEmpty()) throw new IllegalArgumentException("新生儿病案不存在");
        Map<String, Object> result = new LinkedHashMap<>(rows.getFirst());
        result.put("summary", jdbc.queryForMap("""
                SELECT presenting_symptoms AS presentingSymptoms,laboratory_findings AS laboratoryFindings,
                special_examinations AS specialExaminations,treatment_course AS treatmentCourse,complications,
                discharge_condition AS dischargeCondition,discharge_orders AS dischargeOrders,discharge_guidance AS dischargeGuidance
                FROM neonatal_summary WHERE record_id=?
                """, id));
        result.put("issues", jdbc.queryForList("SELECT issue_code AS code,field_name AS field,message FROM neonatal_quality_issue WHERE record_id=? ORDER BY id", id));
        String raw = jdbc.queryForObject("SELECT raw_data FROM neonatal_source_row WHERE id=(SELECT source_row_id FROM neonatal_record WHERE id=?)", String.class, id);
        try { result.put("rawData", json.readValue(raw, Map.class)); }
        catch (JsonProcessingException e) { throw new IllegalStateException("原始行数据无法读取", e); }
        result.put("batch", batch(((Number) result.get("batchId")).longValue()));
        return result;
    }

    public Map<String, Object> stats(Map<String, String> values) {
        long batch = selectedBatch(values);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("batch", batch(batch));
        result.put("overview", jdbc.queryForMap("""
                SELECT COUNT(*) AS records,COUNT(DISTINCT NULLIF(hospital_no,'')) AS hospitalNumbers,
                COUNT(gestational_weeks) AS knownGestation,COALESCE(SUM(gestational_weeks<37),0) AS preterm,
                COALESCE(SUM(issue_count>0),0) AS recordsWithIssues,COUNT(length_of_stay) AS validStayRecords,
                ROUND(AVG(length_of_stay),1) AS averageStay,COALESCE(SUM(admission_time IS NULL),0) AS unknownAdmissionDate
                FROM neonatal_record WHERE batch_id=?
                """, batch));
        result.put("gestation", jdbc.queryForList("""
                SELECT COALESCE(CAST(gestational_weeks AS CHAR),'未提及') AS label,COUNT(*) AS count
                FROM neonatal_record WHERE batch_id=? GROUP BY gestational_weeks ORDER BY gestational_weeks IS NULL,gestational_weeks
                """, batch));
        result.put("monthly", jdbc.queryForList("""
                SELECT DATE_FORMAT(admission_time,'%Y-%m') AS label,COUNT(*) AS count FROM neonatal_record
                WHERE batch_id=? AND admission_time IS NOT NULL GROUP BY label ORDER BY label
                """, batch));
        result.put("outcomes", jdbc.queryForList("SELECT outcome_group AS label,COUNT(*) AS count FROM neonatal_record WHERE batch_id=? GROUP BY outcome_group ORDER BY count DESC", batch));
        result.put("gender", jdbc.queryForList("SELECT gender AS label,COUNT(*) AS count FROM neonatal_record WHERE batch_id=? GROUP BY gender ORDER BY count DESC", batch));
        result.put("diagnoses", jdbc.queryForList("""
                SELECT d.diagnosis_text AS label,COUNT(*) AS count FROM neonatal_diagnosis d
                JOIN neonatal_record r ON r.id=d.record_id WHERE r.batch_id=? AND d.phase='DISCHARGE' AND d.diagnosis_text<>''
                GROUP BY d.diagnosis_text ORDER BY count DESC,label LIMIT 10
                """, batch));
        return result;
    }

    public Map<String, Object> quality(Map<String, String> values) {
        long batch = selectedBatch(values);
        var counts = jdbc.queryForList("""
                SELECT q.issue_code AS code,q.field_name AS field,q.message,COUNT(*) AS count
                FROM neonatal_quality_issue q JOIN neonatal_record r ON r.id=q.record_id
                WHERE r.batch_id=? GROUP BY q.issue_code,q.field_name,q.message ORDER BY count DESC
                """, batch);
        Map<String, String> filter = new HashMap<>(values);
        filter.put("issuesOnly", "true");
        Long total = jdbc.queryForObject("SELECT COUNT(*) FROM neonatal_record WHERE batch_id=? AND issue_count>0", Long.class, batch);
        return Map.of("batch", batch(batch), "counts", counts, "totalIssueRecords", Objects.requireNonNull(total), "page", records(filter));
    }
}

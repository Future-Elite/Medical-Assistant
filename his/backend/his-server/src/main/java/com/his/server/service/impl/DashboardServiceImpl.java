package com.his.server.service.impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.PathwayReport;
import com.his.common.dto.RationalDrugReport;
import com.his.common.entity.Patient;
import com.his.common.mapper.AdmissionMapper;
import com.his.common.mapper.PatientMapper;
import com.his.common.mapper.PrescriptionItemMapper;
import com.his.common.mapper.VisitMapper;
import com.his.server.service.DashboardService;
import com.his.server.service.ValidationService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Supplier;

/**
 * 数据看板统计：随机病人、每月就诊数、药品使用分布。
 *
 * <p>统计口径为 SQL 聚合（{@code GROUP BY} / {@code ORDER BY RAND() LIMIT 1}），
 * 由数据库完成分组与排序，不再把全表拉进内存；两项统计另按设计文档第 9 节缓存
 * {@code stats:visit-frequency} / {@code stats:drug-distribution}，TTL 10 分钟。
 *
 * <p>缓存是纯优化：Redis 不可用时退化为直接查库，绝不抛异常，也不影响返回结构。
 *
 * <p>返回结构是硬约定（前端 {@code Dashboard.vue} 直接读 {@code date}/{@code count}
 * 与 {@code drugName}/{@code count}），不得更改。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DashboardServiceImpl implements DashboardService {

    /** 药品分布最多返回的条数。 */
    private static final int DRUG_DISTRIBUTION_LIMIT = 20;

    /** 设计文档第 9 节规定的看板统计缓存键与 TTL。 */
    private static final String STATS_VISIT_FREQUENCY = "stats:visit-frequency";
    private static final String STATS_DRUG_DISTRIBUTION = "stats:drug-distribution";
    private static final String STATS_ADMISSION_RATIO = "stats:admission-ratio";
    private static final String STATS_RATIONAL_DRUG = "stats:rational-drug-rate";
    private static final String STATS_PATHWAY = "stats:pathway-rate";
    private static final Duration STATS_TTL = Duration.ofMinutes(10);

    private static final TypeReference<List<Map<String, Object>>> ROWS_TYPE =
            new TypeReference<>() { };

    /** 注意：字段顺序即构造器参数顺序，被 DashboardServiceTest 按位置依赖。 */
    private final PatientMapper patientMapper;
    private final VisitMapper visitMapper;
    private final PrescriptionItemMapper itemMapper;
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;
    private final AdmissionMapper admissionMapper;
    private final ValidationService validationService;

    @Override
    public Patient randomPatient() {
        Long id = patientMapper.selectRandomId();
        return id == null ? null : patientMapper.selectById(id);
    }

    @Override
    public List<Map<String, Object>> visitFrequency() {
        List<Map<String, Object>> cached = readCache(STATS_VISIT_FREQUENCY);
        if (cached != null) {
            return cached;
        }
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : visitMapper.selectMonthlyCounts()) {
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("date", asString(pick(row, "d")));
            r.put("count", asLong(pick(row, "c")));
            rows.add(r);
        }
        writeCache(STATS_VISIT_FREQUENCY, rows);
        return rows;
    }

    @Override
    public List<Map<String, Object>> drugDistribution() {
        List<Map<String, Object>> cached = readCache(STATS_DRUG_DISTRIBUTION);
        if (cached != null) {
            return cached;
        }
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : itemMapper.selectDrugDistribution(DRUG_DISTRIBUTION_LIMIT)) {
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("drugName", asString(pick(row, "drugName")));
            r.put("count", asLong(pick(row, "cnt")));
            rows.add(r);
        }
        writeCache(STATS_DRUG_DISTRIBUTION, rows);
        return rows;
    }

    // -------------------------------------------------------------- 看板新增统计

    /** 住院占比：至少住院过一次的患者数 / 患者总数。 */
    @Override
    public Map<String, Object> admissionRatio() {
        long admissionPatients = admissionMapper.selectAdmissionPatientCount();
        long totalPatients = patientMapper.selectCount(null);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("admissionPatients", admissionPatients);
        m.put("totalPatients", totalPatients);
        m.put("ratio", totalPatients == 0 ? 0.0 : (double) admissionPatients / totalPatients);
        return m;
    }

    /** 合理用药率：复用 Task 8 的校验报告，按 10 分钟缓存。 */
    @Override
    public Map<String, Object> rationalDrugRate() {
        return cached(STATS_RATIONAL_DRUG, () -> {
            RationalDrugReport r = validationService.run().getRationalDrug();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("totalPrescriptions", r.getTotalPrescriptions());
            m.put("violationPrescriptions", r.getViolationPrescriptions());
            m.put("rationalRate", r.getRationalRate());
            return m;
        });
    }

    /** 路径变异率：复用 Task 8 的校验报告，按 10 分钟缓存。 */
    @Override
    public Map<String, Object> pathwayRate() {
        return cached(STATS_PATHWAY, () -> {
            PathwayReport p = validationService.run().getPathway();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("enrolledAdmissions", p.getEnrolledAdmissions());
            m.put("executionRate", p.getExecutionRate());
            return m;
        });
    }

    /**
     * 对象型统计的通用缓存：命中直接返回；未命中回调源计算并写回。
     * Redis 读、写失败都只退化到回源，不影响响应。
     */
    private Map<String, Object> cached(String key, Supplier<Map<String, Object>> loader) {
        String json;
        try {
            json = redis.opsForValue().get(key);
            if (json != null) {
                return objectMapper.readValue(json, new TypeReference<Map<String, Object>>() { });
            }
        } catch (Exception ignored) {
            log.warn("读取看板统计缓存失败，改为回源计算: key={}", key, ignored.toString());
        }
        Map<String, Object> m = loader.get();
        try {
            redis.opsForValue().set(key, objectMapper.writeValueAsString(m), STATS_TTL);
        } catch (Exception ignored) {
            log.warn("写入看板统计缓存失败（本次响应不受影响）: key={}", key, ignored.toString());
        }
        return m;
    }

    // ---------------------------------------------------------------- 缓存与取值

    /**
     * 读统计缓存。返回 {@code null} 表示「未命中或 Redis 不可用」，
     * 调用方一律回源查库——Redis 故障不得让看板接口失败。
     */
    private List<Map<String, Object>> readCache(String key) {
        String json;
        try {
            json = redis.opsForValue().get(key);
        } catch (RedisConnectionFailureException e) {
            log.warn("Redis 连接失败，看板统计缓存不可用，改为直接查库: key={} ({})", key, e.toString());
            return null;
        } catch (Exception e) {
            log.warn("读取看板统计缓存异常，改为直接查库: key={}", key, e);
            return null;
        }
        if (json == null) {
            return null;
        }
        try {
            List<Map<String, Object>> rows = objectMapper.readValue(json, ROWS_TYPE);
            // 反序列化后小整数会退化成 Integer，统一回写成 Long，保证与未命中路径的类型一致
            for (Map<String, Object> row : rows) {
                if (row.containsKey("count")) {
                    row.put("count", asLong(row.get("count")));
                }
            }
            return rows;
        } catch (Exception e) {
            log.warn("看板统计缓存内容不可解析，忽略缓存并回源: key={}", key, e);
            return null;
        }
    }

    /** 写统计缓存；失败只记日志，不影响本次响应。 */
    private void writeCache(String key, List<Map<String, Object>> rows) {
        try {
            redis.opsForValue().set(key, objectMapper.writeValueAsString(rows), STATS_TTL);
        } catch (RedisConnectionFailureException e) {
            log.warn("Redis 连接失败，看板统计缓存写入跳过（本次响应不受影响）: key={} ({})", key, e.toString());
        } catch (Exception e) {
            log.warn("写入看板统计缓存异常（本次响应不受影响）: key={}", key, e);
        }
    }

    /** 按别名取值，忽略大小写差异（MyBatis 的 Map key 来自 JDBC 列标签）。 */
    private static Object pick(Map<String, Object> row, String alias) {
        if (row == null) {
            return null;
        }
        Object v = row.get(alias);
        if (v != null) {
            return v;
        }
        String lower = alias.toLowerCase(Locale.ROOT);
        for (Map.Entry<String, Object> e : row.entrySet()) {
            if (e.getKey() != null && e.getKey().toLowerCase(Locale.ROOT).equals(lower)) {
                return e.getValue();
            }
        }
        return null;
    }

    private static Long asLong(Object v) {
        return v == null ? null : ((Number) v).longValue();
    }

    private static String asString(Object v) {
        return v == null ? null : String.valueOf(v);
    }
}

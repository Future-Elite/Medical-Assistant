package com.his.server.service.impl;

import com.his.common.entity.Patient;
import com.his.common.entity.PrescriptionItem;
import com.his.common.entity.Visit;
import com.his.common.mapper.PatientMapper;
import com.his.common.mapper.PrescriptionItemMapper;
import com.his.common.mapper.VisitMapper;
import com.his.server.service.DashboardService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ThreadLocalRandom;
import java.util.stream.Collectors;

/**
 * 数据看板统计：随机病人、每月就诊数、药品使用分布。
 *
 * <p>统计口径为内存聚合（先 selectList 再分组），在演示数据规模下足够；
 * 数据量大时应下沉为 SQL 的 GROUP BY / ORDER BY RAND() LIMIT 1。
 */
@Service
@RequiredArgsConstructor
public class DashboardServiceImpl implements DashboardService {

    /** 药品分布最多返回的条数。 */
    private static final int DRUG_DISTRIBUTION_LIMIT = 20;

    private static final DateTimeFormatter MONTH_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM");

    /** 注意：字段顺序即构造器参数顺序，被 DashboardServiceTest 按位置依赖。 */
    private final PatientMapper patientMapper;
    private final VisitMapper visitMapper;
    private final PrescriptionItemMapper itemMapper;

    @Override
    public Patient randomPatient() {
        List<Patient> patients = patientMapper.selectList(null);
        if (patients == null || patients.isEmpty()) {
            return null;
        }
        List<Long> ids = patients.stream()
                .map(Patient::getId)
                .filter(Objects::nonNull)
                .collect(Collectors.toList());
        if (ids.isEmpty()) {
            return null;
        }
        Long id = ids.get(ThreadLocalRandom.current().nextInt(ids.size()));
        return patientMapper.selectById(id);
    }

    @Override
    public List<Map<String, Object>> visitFrequency() {
        List<Visit> visits = visitMapper.selectList(null);
        if (visits == null || visits.isEmpty()) {
            return new ArrayList<>();
        }

        Map<String, Long> counts = visits.stream()
                .map(Visit::getVisitTime)
                .filter(Objects::nonNull)
                .collect(Collectors.groupingBy(MONTH_FORMAT::format, Collectors.counting()));

        return counts.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .map(e -> {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("date", e.getKey());
                    row.put("count", e.getValue());
                    return row;
                })
                .collect(Collectors.toList());
    }

    @Override
    public List<Map<String, Object>> drugDistribution() {
        List<PrescriptionItem> items = itemMapper.selectList(null);
        if (items == null || items.isEmpty()) {
            return new ArrayList<>();
        }

        Map<String, Long> counts = items.stream()
                .map(PrescriptionItem::getDrugName)
                .filter(n -> n != null && !n.isBlank())
                .collect(Collectors.groupingBy(n -> n, Collectors.counting()));

        return counts.entrySet().stream()
                .sorted(Map.Entry.<String, Long>comparingByValue(Comparator.reverseOrder())
                        .thenComparing(Map.Entry.comparingByKey()))
                .limit(DRUG_DISTRIBUTION_LIMIT)
                .map(e -> {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("drugName", e.getKey());
                    row.put("count", e.getValue());
                    return row;
                })
                .collect(Collectors.toList());
    }
}

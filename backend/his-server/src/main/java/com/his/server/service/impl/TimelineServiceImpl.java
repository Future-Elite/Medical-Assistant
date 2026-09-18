package com.his.server.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.TimelineEvent;
import com.his.common.dto.TimelineVO;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.server.service.TimelineService;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class TimelineServiceImpl implements TimelineService {

    private static final String CACHE_PREFIX = "timeline:";
    private static final Duration TTL = Duration.ofMinutes(30);

    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final LabTestMapper labTestMapper;
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;

    @Override
    public TimelineVO getTimeline(Long patientId) {
        String key = CACHE_PREFIX + patientId;
        String cached = redis.opsForValue().get(key);
        if (cached != null) {
            try {
                return objectMapper.readValue(cached, TimelineVO.class);
            } catch (Exception ignored) { /* fall through to rebuild */ }
        }
        TimelineVO vo = build(patientId);
        try {
            redis.opsForValue().set(key, objectMapper.writeValueAsString(vo), TTL);
        } catch (Exception ignored) { }
        return vo;
    }

    private TimelineVO build(Long patientId) {
        List<TimelineEvent> events = new ArrayList<>();

        visitMapper.selectList(new LambdaQueryWrapper<Visit>().eq(Visit::getPatientId, patientId))
                .forEach(v -> events.add(new TimelineEvent("VISIT", v.getVisitTime(), v)));

        diagnosisMapper.selectList(new LambdaQueryWrapper<Diagnosis>().eq(Diagnosis::getPatientId, patientId))
                .forEach(d -> events.add(new TimelineEvent("DIAGNOSIS", d.getDiagnosedAt(), d)));

        List<Prescription> pres = prescriptionMapper.selectList(
                new LambdaQueryWrapper<Prescription>().eq(Prescription::getPatientId, patientId));
        Map<Long, List<PrescriptionItem>> itemMap = pres.isEmpty() ? Map.of() :
                itemMapper.selectList(new LambdaQueryWrapper<PrescriptionItem>()
                        .in(PrescriptionItem::getPrescriptionId,
                            pres.stream().map(Prescription::getId).collect(Collectors.toList())))
                        .stream().collect(Collectors.groupingBy(PrescriptionItem::getPrescriptionId));
        for (Prescription p : pres) {
            p.setItems(itemMap.getOrDefault(p.getId(), List.of()));
            events.add(new TimelineEvent("PRESCRIPTION", p.getPrescribedAt(), p));
        }

        labTestMapper.selectList(new LambdaQueryWrapper<LabTest>().eq(LabTest::getPatientId, patientId))
                .forEach(l -> events.add(new TimelineEvent("LAB_TEST", l.getTestedAt(), l)));

        events.sort(Comparator.comparing(TimelineEvent::getTime));
        return new TimelineVO(patientId, events);
    }
}

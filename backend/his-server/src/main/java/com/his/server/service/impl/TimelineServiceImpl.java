package com.his.server.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.TimelineEvent;
import com.his.common.dto.TimelineVO;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.server.service.TimelineService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class TimelineServiceImpl implements TimelineService {

    private static final String CACHE_PREFIX = "timeline:";
    private static final String ADMISSION_CACHE_PREFIX = "timeline:admission:";
    private static final Duration TTL = Duration.ofMinutes(30);

    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final LabTestMapper labTestMapper;
    private final AdmissionMapper admissionMapper;
    private final MedicalOrderMapper medicalOrderMapper;
    private final VitalSignMapper vitalSignMapper;
    private final ChargeMapper chargeMapper;
    private final DispenseMapper dispenseMapper;
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;

    /**
     * 缓存只是优化，不是依赖：Redis 不可用时必须退化为「直接查库重建」，
     * 绝不能让 GET /api/patients/{id}/timeline 变成 500。
     */
    @Override
    public TimelineVO getTimeline(Long patientId) {
        String key = CACHE_PREFIX + patientId;
        String cached = null;
        try {
            cached = redis.opsForValue().get(key);
        } catch (RedisConnectionFailureException e) {
            // 连接类故障是「高频且自解释」的，只记一行消息，避免 Redis 宕机时刷爆日志
            log.warn("Redis 连接失败，时间线缓存不可用，改为直接查库重建: patientId={} ({})",
                    patientId, e.toString());
        } catch (Exception e) {
            log.warn("Redis 读取时间线缓存异常，改为直接查库重建: patientId={}", patientId, e);
        }
        if (cached != null) {
            try {
                return objectMapper.readValue(cached, TimelineVO.class);
            } catch (Exception e) {
                log.warn("时间线缓存反序列化失败，忽略缓存内容并重建: patientId={}", patientId, e);
            }
        }
        TimelineVO vo = build(patientId);
        try {
            redis.opsForValue().set(key, objectMapper.writeValueAsString(vo), TTL);
        } catch (RedisConnectionFailureException e) {
            log.warn("Redis 连接失败，时间线缓存写入跳过（本次响应不受影响）: patientId={} ({})",
                    patientId, e.toString());
        } catch (Exception e) {
            log.warn("Redis 写入时间线缓存异常（本次响应不受影响）: patientId={}", patientId, e);
        }
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

        labTestMapper.selectList(new LambdaQueryWrapper<LabTest>()
                        .eq(LabTest::getPatientId, patientId)
                        .isNull(LabTest::getAdmissionId))
                .forEach(l -> events.add(new TimelineEvent("LAB_TEST", l.getTestedAt(), l)));

        admissionMapper.selectList(new LambdaQueryWrapper<Admission>().eq(Admission::getPatientId, patientId))
                .forEach(a -> {
                    events.add(new TimelineEvent("ADMISSION", a.getAdmissionTime(), a));
                    if (a.getDischargeTime() != null) {
                        events.add(new TimelineEvent("DISCHARGE", a.getDischargeTime(), a));
                    }
                });

        chargeMapper.selectList(new LambdaQueryWrapper<Charge>().eq(Charge::getPatientId, patientId))
                .forEach(c -> events.add(new TimelineEvent("CHARGE", c.getChargedAt(), c)));

        dispenseMapper.selectList(new LambdaQueryWrapper<Dispense>().eq(Dispense::getPatientId, patientId))
                .forEach(d -> events.add(new TimelineEvent("DISPENSE", d.getDispensedAt(), d)));

        events.sort(Comparator.comparing(TimelineEvent::getTime));
        return new TimelineVO(patientId, events);
    }

    @Override
    public TimelineVO getAdmissionTimeline(Long admissionId) {
        String key = ADMISSION_CACHE_PREFIX + admissionId;
        String cached = null;
        try {
            cached = redis.opsForValue().get(key);
        } catch (Exception e) {
            log.warn("Redis 读取住院时间轴缓存异常，改为直接查库重建: admissionId={}", admissionId, e);
        }
        if (cached != null) {
            try {
                return objectMapper.readValue(cached, TimelineVO.class);
            } catch (Exception e) {
                log.warn("住院时间轴缓存反序列化失败，忽略缓存内容并重建: admissionId={}", admissionId, e);
            }
        }
        TimelineVO vo = buildAdmission(admissionId);
        try {
            redis.opsForValue().set(key, objectMapper.writeValueAsString(vo), TTL);
        } catch (Exception e) {
            log.warn("Redis 写入住院时间轴缓存异常（本次响应不受影响）: admissionId={}", admissionId, e);
        }
        return vo;
    }

    private TimelineVO buildAdmission(Long admissionId) {
        Admission admission = admissionMapper.selectById(admissionId);
        if (admission == null) {
            return new TimelineVO(null, List.of());
        }
        List<TimelineEvent> events = new ArrayList<>();

        medicalOrderMapper.selectList(new LambdaQueryWrapper<MedicalOrder>()
                        .eq(MedicalOrder::getAdmissionId, admissionId))
                .forEach(o -> events.add(new TimelineEvent("ORDER", o.getStartTime(), o)));

        vitalSignMapper.selectList(new LambdaQueryWrapper<VitalSign>()
                        .eq(VitalSign::getAdmissionId, admissionId))
                .forEach(v -> events.add(new TimelineEvent("VITAL_SIGN", v.getRecordedAt(), v)));

        labTestMapper.selectList(new LambdaQueryWrapper<LabTest>()
                        .eq(LabTest::getAdmissionId, admissionId))
                .forEach(l -> events.add(new TimelineEvent("LAB_TEST", l.getTestedAt(), l)));

        events.sort(Comparator.comparing(TimelineEvent::getTime));
        return new TimelineVO(admission.getPatientId(), events);
    }
}

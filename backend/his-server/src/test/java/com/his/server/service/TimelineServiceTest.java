package com.his.server.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.TimelineVO;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.server.service.impl.TimelineServiceImpl;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class TimelineServiceTest {

    @Test
    void events_are_sorted_ascending_by_time() {
        VisitMapper vm = mock(VisitMapper.class);
        DiagnosisMapper dm = mock(DiagnosisMapper.class);
        PrescriptionMapper pm = mock(PrescriptionMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        LabTestMapper lm = mock(LabTestMapper.class);
        StringRedisTemplate redis = mock(StringRedisTemplate.class);
        when(redis.opsForValue()).thenReturn(mock(ValueOperations.class));
        ObjectMapper om = new ObjectMapper();

        Visit v = new Visit(); v.setId(1L); v.setPatientId(1L); v.setVisitTime(LocalDateTime.of(2024,1,1,10,0));
        LabTest l = new LabTest(); l.setId(1L); l.setPatientId(1L); l.setTestedAt(LocalDateTime.of(2024,1,1,9,30));
        when(vm.selectList(any())).thenReturn(List.of(v));
        when(dm.selectList(any())).thenReturn(List.of());
        when(pm.selectList(any())).thenReturn(List.of());
        when(im.selectList(any())).thenReturn(List.of());
        when(lm.selectList(any())).thenReturn(List.of(l));

        TimelineServiceImpl svc = new TimelineServiceImpl(vm, dm, pm, im, lm, redis, om);
        TimelineVO vo = svc.getTimeline(1L);

        assertThat(vo.getEvents()).hasSize(2);
        assertThat(vo.getEvents().get(0).getType()).isEqualTo("LAB_TEST");
        assertThat(vo.getEvents().get(1).getType()).isEqualTo("VISIT");
    }
}

package com.his.server.service;

import com.his.common.entity.Admission;
import com.his.common.mapper.AdmissionMapper;
import com.his.server.service.impl.AdmissionServiceImpl;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class AdmissionServiceImplTest {

    @Test
    void discharge_sets_discharge_time_total_cost_and_status() {
        AdmissionMapper mapper = mock(AdmissionMapper.class);
        CacheInvalidator inv = mock(CacheInvalidator.class);
        Admission a = new Admission();
        a.setId(1L); a.setPatientId(42L); a.setStatus(0);
        when(mapper.selectById(1L)).thenReturn(a);
        when(mapper.updateById(a)).thenReturn(1);

        AdmissionServiceImpl svc = new AdmissionServiceImpl(inv);
        org.springframework.test.util.ReflectionTestUtils.setField(svc, "baseMapper", mapper);

        Admission out = svc.discharge(1L, new BigDecimal("1234.56"));

        assertThat(out.getStatus()).isEqualTo(1);
        assertThat(out.getDischargeTime()).isNotNull();
        assertThat(out.getTotalCost()).isEqualByComparingTo("1234.56");
        verify(inv).evictTimeline(42L);
    }

    @Test
    void discharge_returns_null_when_admission_absent() {
        AdmissionMapper mapper = mock(AdmissionMapper.class);
        when(mapper.selectById(99L)).thenReturn(null);
        AdmissionServiceImpl svc = new AdmissionServiceImpl(mock(CacheInvalidator.class));
        org.springframework.test.util.ReflectionTestUtils.setField(svc, "baseMapper", mapper);

        assertThat(svc.discharge(99L, BigDecimal.ZERO)).isNull();
    }
}

package com.his.server.service;

import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.server.service.impl.DashboardServiceImpl;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class DashboardServiceTest {

    @Test
    void drugDistribution_groups_and_sorts_by_count_desc() {
        VisitMapper vm = mock(VisitMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        PatientMapper pm = mock(PatientMapper.class);

        PrescriptionItem a = new PrescriptionItem(); a.setDrugName("感冒灵颗粒");
        PrescriptionItem b = new PrescriptionItem(); b.setDrugName("布洛芬");
        PrescriptionItem c = new PrescriptionItem(); c.setDrugName("感冒灵颗粒");
        when(im.selectList(any())).thenReturn(List.of(a, b, c));

        DashboardServiceImpl svc = new DashboardServiceImpl(pm, vm, im);
        List<Map<String, Object>> dist = svc.drugDistribution();

        assertThat(dist).hasSize(2);
        assertThat(dist.get(0).get("drugName")).isEqualTo("感冒灵颗粒");
        assertThat(dist.get(0).get("count")).isEqualTo(2L);
        assertThat(dist.get(1).get("drugName")).isEqualTo("布洛芬");
    }

    @Test
    void visitFrequency_merges_same_month() {
        VisitMapper vm = mock(VisitMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        PatientMapper pm = mock(PatientMapper.class);

        Visit v1 = new Visit(); v1.setVisitTime(LocalDateTime.of(2024, 1, 5, 9, 0));
        Visit v2 = new Visit(); v2.setVisitTime(LocalDateTime.of(2024, 1, 20, 10, 0));
        Visit v3 = new Visit(); v3.setVisitTime(LocalDateTime.of(2024, 2, 1, 11, 0));
        when(vm.selectList(any())).thenReturn(List.of(v1, v2, v3));

        DashboardServiceImpl svc = new DashboardServiceImpl(pm, vm, im);
        List<Map<String, Object>> freq = svc.visitFrequency();

        assertThat(freq).hasSize(2);
        assertThat(freq.get(0).get("date")).isEqualTo("2024-01");
        assertThat(freq.get(0).get("count")).isEqualTo(2L);
    }
}

package com.his.server.service;

import com.his.common.entity.LabTest;
import com.his.common.mapper.LabTestMapper;
import com.his.server.service.impl.LabTestServiceImpl;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.mockito.Mockito.*;

class LabTestServiceImplTest {

    @Test
    void save_evicts_patient_and_admission_timelines_when_admitted() {
        LabTestMapper mapper = mock(LabTestMapper.class);
        CacheInvalidator inv = mock(CacheInvalidator.class);
        LabTest l = new LabTest(); l.setPatientId(42L); l.setAdmissionId(9L);

        LabTestServiceImpl svc = new LabTestServiceImpl(inv);
        ReflectionTestUtils.setField(svc, "baseMapper", mapper);

        svc.save(l);

        verify(inv).evictTimeline(42L);
        verify(inv).evictAdmissionTimeline(9L);
    }

    /**
     * 门诊化验单的 admissionId 为 NULL：此时 cacheInvalidator 不能拿 null 去拼
     * "timeline:admission:null" 这种无意义的键。
     */
    @Test
    void save_skips_admission_timeline_eviction_for_outpatient_lab() {
        LabTestMapper mapper = mock(LabTestMapper.class);
        CacheInvalidator inv = mock(CacheInvalidator.class);
        LabTest l = new LabTest(); l.setPatientId(42L); // admissionId 保持 null

        LabTestServiceImpl svc = new LabTestServiceImpl(inv);
        ReflectionTestUtils.setField(svc, "baseMapper", mapper);

        svc.save(l);

        verify(inv).evictTimeline(42L);
        verify(inv, never()).evictAdmissionTimeline(any());
    }
}

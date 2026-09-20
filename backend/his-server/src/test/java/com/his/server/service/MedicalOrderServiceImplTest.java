package com.his.server.service;

import com.his.common.entity.MedicalOrder;
import com.his.common.mapper.MedicalOrderMapper;
import com.his.server.service.impl.MedicalOrderServiceImpl;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.mockito.Mockito.*;

class MedicalOrderServiceImplTest {

    @Test
    void save_evicts_patient_and_admission_timelines() {
        MedicalOrderMapper mapper = mock(MedicalOrderMapper.class);
        CacheInvalidator inv = mock(CacheInvalidator.class);
        MedicalOrder o = new MedicalOrder(); o.setPatientId(42L); o.setAdmissionId(9L);

        MedicalOrderServiceImpl svc = new MedicalOrderServiceImpl(inv);
        ReflectionTestUtils.setField(svc, "baseMapper", mapper);

        svc.save(o);

        verify(inv).evictTimeline(42L);
        verify(inv).evictAdmissionTimeline(9L);
    }
}

package com.his.server.service;

import com.his.common.entity.VitalSign;
import com.his.common.mapper.VitalSignMapper;
import com.his.server.service.impl.VitalSignServiceImpl;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.mockito.Mockito.*;

class VitalSignServiceImplTest {

    @Test
    void save_evicts_patient_and_admission_timelines() {
        VitalSignMapper mapper = mock(VitalSignMapper.class);
        CacheInvalidator inv = mock(CacheInvalidator.class);
        VitalSign v = new VitalSign(); v.setPatientId(42L); v.setAdmissionId(9L);

        VitalSignServiceImpl svc = new VitalSignServiceImpl(inv);
        ReflectionTestUtils.setField(svc, "baseMapper", mapper);

        svc.save(v);

        verify(inv).evictTimeline(42L);
        verify(inv).evictAdmissionTimeline(9L);
    }
}

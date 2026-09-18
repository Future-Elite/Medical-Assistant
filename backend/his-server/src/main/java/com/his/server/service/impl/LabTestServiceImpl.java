package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.LabTest;
import com.his.common.mapper.LabTestMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.LabTestService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class LabTestServiceImpl extends ServiceImpl<LabTestMapper, LabTest> implements LabTestService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(LabTest entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }
}

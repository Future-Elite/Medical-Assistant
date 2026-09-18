package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Diagnosis;
import com.his.common.mapper.DiagnosisMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.DiagnosisService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class DiagnosisServiceImpl extends ServiceImpl<DiagnosisMapper, Diagnosis> implements DiagnosisService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(Diagnosis entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }
}

package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Visit;
import com.his.common.mapper.VisitMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.VisitService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class VisitServiceImpl extends ServiceImpl<VisitMapper, Visit> implements VisitService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(Visit entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }
}

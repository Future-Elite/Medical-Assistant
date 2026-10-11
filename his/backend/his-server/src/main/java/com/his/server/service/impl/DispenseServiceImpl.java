package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Dispense;
import com.his.common.mapper.DispenseMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.DispenseService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class DispenseServiceImpl extends ServiceImpl<DispenseMapper, Dispense> implements DispenseService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(Dispense entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }
}

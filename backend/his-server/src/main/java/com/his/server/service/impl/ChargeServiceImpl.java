package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Charge;
import com.his.common.mapper.ChargeMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.ChargeService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ChargeServiceImpl extends ServiceImpl<ChargeMapper, Charge> implements ChargeService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(Charge entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }
}

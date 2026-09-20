package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.VitalSign;
import com.his.common.mapper.VitalSignMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.VitalSignService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class VitalSignServiceImpl extends ServiceImpl<VitalSignMapper, VitalSign>
        implements VitalSignService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(VitalSign entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }
}

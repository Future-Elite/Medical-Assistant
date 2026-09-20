package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.MedicalOrder;
import com.his.common.mapper.MedicalOrderMapper;
import com.his.server.service.CacheInvalidator;
import com.his.server.service.MedicalOrderService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class MedicalOrderServiceImpl extends ServiceImpl<MedicalOrderMapper, MedicalOrder>
        implements MedicalOrderService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(MedicalOrder entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        cacheInvalidator.evictAdmissionTimeline(entity.getAdmissionId());
        return saved;
    }

    @Override
    public MedicalOrder stop(Long id) {
        MedicalOrder o = getById(id);
        if (o == null) {
            return null;
        }
        o.setStopTime(LocalDateTime.now());
        o.setStatus(1);
        updateById(o);
        cacheInvalidator.evictTimeline(o.getPatientId());
        cacheInvalidator.evictAdmissionTimeline(o.getAdmissionId());
        return o;
    }
}

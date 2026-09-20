package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Admission;
import com.his.common.mapper.AdmissionMapper;
import com.his.server.service.AdmissionService;
import com.his.server.service.CacheInvalidator;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class AdmissionServiceImpl extends ServiceImpl<AdmissionMapper, Admission> implements AdmissionService {

    private final CacheInvalidator cacheInvalidator;

    @Override
    public boolean save(Admission entity) {
        boolean saved = super.save(entity);
        cacheInvalidator.evictTimeline(entity.getPatientId());
        return saved;
    }

    @Override
    public Admission discharge(Long id, BigDecimal totalCost) {
        Admission a = getById(id);
        if (a == null) {
            return null;
        }
        a.setDischargeTime(LocalDateTime.now());
        a.setTotalCost(totalCost == null ? BigDecimal.ZERO : totalCost);
        a.setStatus(1);
        updateById(a);
        cacheInvalidator.evictTimeline(a.getPatientId());
        return a;
    }
}

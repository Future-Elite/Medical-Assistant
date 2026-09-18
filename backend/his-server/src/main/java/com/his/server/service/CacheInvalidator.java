package com.his.server.service;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
public class CacheInvalidator {

    private final StringRedisTemplate redis;

    public void evictTimeline(Long patientId) {
        redis.delete("timeline:" + patientId);
    }
}

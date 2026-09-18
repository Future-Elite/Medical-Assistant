package com.his.server.service;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

/**
 * 患者时间线缓存失效器。
 *
 * <p>失效是「清缓存」而不是「写数据」：Redis 不可用时只应降级为「缓存可能短暂过期」，
 * 绝不能让 POST /api/visits|diagnoses|prescriptions|lab-tests 在数据已落库之后抛 500
 * （客户端会以为写入失败并重试，产生重复数据）。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class CacheInvalidator {

    private final StringRedisTemplate redis;

    public void evictTimeline(Long patientId) {
        try {
            redis.delete("timeline:" + patientId);
        } catch (RedisConnectionFailureException e) {
            log.warn("Redis 连接失败，时间线缓存未能失效（数据已落库，仅缓存可能短暂过期）: patientId={} ({})",
                    patientId, e.toString());
        } catch (Exception e) {
            log.warn("时间线缓存失效异常（数据已落库，仅缓存可能短暂过期）: patientId={}", patientId, e);
        }
    }
}

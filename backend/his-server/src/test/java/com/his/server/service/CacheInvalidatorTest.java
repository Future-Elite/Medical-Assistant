package com.his.server.service;

import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.Mockito.*;

class CacheInvalidatorTest {

    @Test
    void evicts_the_timeline_key() {
        StringRedisTemplate redis = mock(StringRedisTemplate.class);

        new CacheInvalidator(redis).evictTimeline(42L);

        verify(redis).delete("timeline:42");
    }

    /**
     * 重要 #1：失效发生在数据落库「之后」。Redis 挂掉时若把异常抛出去，
     * 客户端会收到 500 而误以为写入失败并重试（产生重复数据）。
     */
    @Test
    void redis_down_does_not_propagate() {
        StringRedisTemplate redis = mock(StringRedisTemplate.class);
        when(redis.delete(anyString())).thenThrow(new RedisConnectionFailureException("connection refused"));
        CacheInvalidator invalidator = new CacheInvalidator(redis);

        assertThatCode(() -> invalidator.evictTimeline(42L)).doesNotThrowAnyException();
    }

    /** 非连接类的 Redis 异常同样只记日志，不影响已完成的写入。 */
    @Test
    void unexpected_redis_error_does_not_propagate() {
        StringRedisTemplate redis = mock(StringRedisTemplate.class);
        when(redis.delete(anyString())).thenThrow(new IllegalStateException("boom"));
        CacheInvalidator invalidator = new CacheInvalidator(redis);

        assertThatCode(() -> invalidator.evictTimeline(42L)).doesNotThrowAnyException();
    }
}

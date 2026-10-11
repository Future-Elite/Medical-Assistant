package com.his.server.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.mapper.*;
import com.his.server.service.impl.DashboardServiceImpl;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

class DashboardServiceTest {

    private VisitMapper vm;
    private PrescriptionItemMapper im;
    private PatientMapper pm;
    private AdmissionMapper admissionMapper;
    private ValidationService validationService;
    private StringRedisTemplate redis;
    private ValueOperations<String, String> ops;

    @SuppressWarnings("unchecked")
    @BeforeEach
    void setUp() {
        vm = mock(VisitMapper.class);
        im = mock(PrescriptionItemMapper.class);
        pm = mock(PatientMapper.class);
        admissionMapper = mock(AdmissionMapper.class);
        validationService = mock(ValidationService.class);
        redis = mock(StringRedisTemplate.class);
        ops = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(ops);
        when(ops.get(anyString())).thenReturn(null);          // 默认：缓存未命中
    }

    private DashboardServiceImpl svc() {
        return new DashboardServiceImpl(pm, vm, im, redis, new ObjectMapper(),
                admissionMapper, validationService);
    }

    private static Map<String, Object> row(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return m;
    }

    // ------------------------------------------------------------ 返回结构（硬约定）

    @Test
    void drugDistribution_returns_drugName_count_in_sql_order() {
        when(im.selectDrugDistribution(anyInt())).thenReturn(List.of(
                row("drugName", "感冒灵颗粒", "cnt", 2L),
                row("drugName", "布洛芬", "cnt", 1L)));

        List<Map<String, Object>> dist = svc().drugDistribution();

        assertThat(dist).hasSize(2);
        assertThat(dist.get(0)).containsExactlyEntriesOf(row("drugName", "感冒灵颗粒", "count", 2L));
        assertThat(dist.get(1).get("drugName")).isEqualTo("布洛芬");
        assertThat(dist.get(1).get("count")).isEqualTo(1L);
        verify(im).selectDrugDistribution(20);
    }

    @Test
    void visitFrequency_returns_date_count_and_keeps_sql_monthly_order() {
        when(vm.selectMonthlyCounts()).thenReturn(List.of(
                row("d", "2024-01", "c", 2L),
                row("d", "2024-02", "c", 1L)));

        List<Map<String, Object>> freq = svc().visitFrequency();

        assertThat(freq).hasSize(2);
        assertThat(freq.get(0)).containsExactlyEntriesOf(row("date", "2024-01", "count", 2L));
        assertThat(freq.get(1).get("date")).isEqualTo("2024-02");
        assertThat(freq.get(1).get("count")).isEqualTo(1L);
    }

    /** 空库时两条统计都返回空列表，而不是 null（前端按数组渲染）。 */
    @Test
    void stats_return_empty_list_on_empty_database() {
        when(vm.selectMonthlyCounts()).thenReturn(List.of());
        when(im.selectDrugDistribution(anyInt())).thenReturn(List.of());

        DashboardServiceImpl svc = svc();
        assertThat(svc.visitFrequency()).isEmpty();
        assertThat(svc.drugDistribution()).isEmpty();
    }

    // ------------------------------------------------------------------- 统计缓存

    /** 设计文档 §9：命中键与 TTL=10 分钟；未命中时回源并写回缓存。 */
    @Test
    void stats_are_written_to_spec_cache_keys_with_ten_minute_ttl() {
        when(vm.selectMonthlyCounts()).thenReturn(List.of(row("d", "2024-01", "c", 2L)));
        when(im.selectDrugDistribution(anyInt())).thenReturn(List.of(row("drugName", "布洛芬", "cnt", 3L)));

        DashboardServiceImpl svc = svc();
        svc.visitFrequency();
        svc.drugDistribution();

        verify(ops).set(eq("stats:visit-frequency"), anyString(), eq(Duration.ofMinutes(10)));
        verify(ops).set(eq("stats:drug-distribution"), anyString(), eq(Duration.ofMinutes(10)));
    }

    /** 缓存命中：不再查库，且结构与计数类型（Long）与未命中路径一致。 */
    @Test
    void stats_cache_hit_skips_the_database_and_keeps_shape() {
        when(ops.get("stats:drug-distribution"))
                .thenReturn("[{\"drugName\":\"感冒灵颗粒\",\"count\":7}]");

        List<Map<String, Object>> dist = svc().drugDistribution();

        assertThat(dist).hasSize(1);
        assertThat(dist.get(0)).containsExactlyEntriesOf(row("drugName", "感冒灵颗粒", "count", 7L));
        verifyNoInteractions(im);
    }

    // ------------------------------------------------------- Redis 不可用时优雅降级

    /** Redis 读、写都抛连接异常时，统计接口仍须返回正确结果且不向调用方抛异常。 */
    @Test
    void stats_degrade_gracefully_when_redis_is_down() {
        when(vm.selectMonthlyCounts()).thenReturn(List.of(row("d", "2024-01", "c", 5L)));
        when(ops.get(anyString())).thenThrow(new RedisConnectionFailureException("redis down"));
        doThrow(new RedisConnectionFailureException("redis down"))
                .when(ops).set(anyString(), anyString(), any(Duration.class));

        DashboardServiceImpl svc = svc();

        List<Map<String, Object>> freq = assertDoesNotThrowAndGet(svc);

        assertThat(freq).hasSize(1);
        assertThat(freq.get(0)).containsExactlyEntriesOf(row("date", "2024-01", "count", 5L));
    }

    private static List<Map<String, Object>> assertDoesNotThrowAndGet(DashboardServiceImpl svc) {
        List<Map<String, Object>>[] out = new List[1];
        assertThatCode(() -> out[0] = svc.visitFrequency()).doesNotThrowAnyException();
        return out[0];
    }

    /** 缓存内容损坏（非 JSON）时应忽略缓存并回源，而不是 500。 */
    @Test
    void stats_fall_back_to_database_when_cache_is_corrupt() {
        when(ops.get("stats:visit-frequency")).thenReturn("not-json");
        when(vm.selectMonthlyCounts()).thenReturn(List.of(row("d", "2024-01", "c", 4L)));

        List<Map<String, Object>> freq = svc().visitFrequency();

        assertThat(freq).hasSize(1);
        assertThat(freq.get(0).get("count")).isEqualTo(4L);
    }

    // -------------------------------------------------------------- 随机患者

    @Test
    void randomPatient_uses_random_id_query_and_returns_that_patient() {
        com.his.common.entity.Patient p = new com.his.common.entity.Patient();
        p.setId(7L); p.setName("张三");
        when(pm.selectRandomId()).thenReturn(7L);
        when(pm.selectById(7L)).thenReturn(p);

        assertThat(svc().randomPatient()).isSameAs(p);
        verify(pm, never()).selectList(any());
    }

    @Test
    void randomPatient_returns_null_on_empty_database() {
        when(pm.selectRandomId()).thenReturn(null);

        assertThat(svc().randomPatient()).isNull();
        verify(pm, never()).selectById(any());
    }

    // -------------------------------------------------------------- 看板新增统计

    @Test
    void admission_ratio_computes_from_counts() {
        when(admissionMapper.selectAdmissionPatientCount()).thenReturn(2000L);
        when(pm.selectCount(null)).thenReturn(10000L);

        Map<String, Object> m = svc().admissionRatio();

        assertThat(m.get("ratio")).isEqualTo(0.2);
        assertThat(m).containsEntry("admissionPatients", 2000L)
                .containsEntry("totalPatients", 10000L);
    }
}

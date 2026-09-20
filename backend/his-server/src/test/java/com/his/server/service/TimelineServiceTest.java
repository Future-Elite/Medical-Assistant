package com.his.server.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.fasterxml.jackson.module.paramnames.ParameterNamesModule;
import com.his.common.dto.TimelineEvent;
import com.his.common.dto.TimelineVO;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.server.service.impl.TimelineServiceImpl;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

class TimelineServiceTest {

    /**
     * 与 Spring Boot 默认 ObjectMapper 等价：注册 JSR-310（实体里有 LocalDateTime）
     * 与参数名模块（{@code @AllArgsConstructor} DTO 的按名反序列化）。
     */
    private static final ObjectMapper OM = JsonMapper.builder()
            .addModule(new ParameterNamesModule())
            .addModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .build();

    private VisitMapper vm;
    private DiagnosisMapper dm;
    private PrescriptionMapper pm;
    private PrescriptionItemMapper im;
    private LabTestMapper lm;
    private AdmissionMapper am;
    private MedicalOrderMapper mom;
    private VitalSignMapper vsm;
    private ChargeMapper cm;
    private DispenseMapper dsm;
    private StringRedisTemplate redis;
    private ValueOperations<String, String> ops;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        vm = mock(VisitMapper.class);
        dm = mock(DiagnosisMapper.class);
        pm = mock(PrescriptionMapper.class);
        im = mock(PrescriptionItemMapper.class);
        lm = mock(LabTestMapper.class);
        am = mock(AdmissionMapper.class);
        mom = mock(MedicalOrderMapper.class);
        vsm = mock(VitalSignMapper.class);
        cm = mock(ChargeMapper.class);
        dsm = mock(DispenseMapper.class);
        redis = mock(StringRedisTemplate.class);
        ops = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(ops);

        Visit v = new Visit(); v.setId(1L); v.setPatientId(1L); v.setVisitTime(LocalDateTime.of(2024, 1, 1, 10, 0));
        LabTest l = new LabTest(); l.setId(1L); l.setPatientId(1L); l.setTestedAt(LocalDateTime.of(2024, 1, 1, 9, 30));
        lenient().when(vm.selectList(any())).thenReturn(List.of(v));
        lenient().when(dm.selectList(any())).thenReturn(List.of());
        lenient().when(pm.selectList(any())).thenReturn(List.of());
        lenient().when(im.selectList(any())).thenReturn(List.of());
        lenient().when(lm.selectList(any())).thenReturn(List.of(l));
        lenient().when(am.selectList(any())).thenReturn(List.of());
        lenient().when(mom.selectList(any())).thenReturn(List.of());
        lenient().when(vsm.selectList(any())).thenReturn(List.of());
        lenient().when(cm.selectList(any())).thenReturn(List.of());
        lenient().when(dsm.selectList(any())).thenReturn(List.of());
    }

    private TimelineServiceImpl svc() {
        return new TimelineServiceImpl(vm, dm, pm, im, lm, am, mom, vsm, cm, dsm, redis, OM);
    }

    @Test
    void events_are_sorted_ascending_by_time() {
        TimelineVO vo = svc().getTimeline(1L);

        assertThat(vo.getEvents()).hasSize(2);
        assertThat(vo.getEvents().get(0).getType()).isEqualTo("LAB_TEST");
        assertThat(vo.getEvents().get(1).getType()).isEqualTo("VISIT");
    }

    /** 缓存命中时直接反序列化返回，不再查库、也不回写。 */
    @Test
    void cache_hit_returns_cached_timeline_without_touching_the_database() throws Exception {
        when(ops.get("timeline:1"))
                .thenReturn(OM.writeValueAsString(new TimelineVO(1L, List.of())));

        TimelineVO vo = svc().getTimeline(1L);

        assertThat(vo.getPatientId()).isEqualTo(1L);
        assertThat(vo.getEvents()).isEmpty();
        verifyNoInteractions(vm, dm, pm, im, lm);
        verify(ops, never()).set(anyString(), anyString(), any(Duration.class));
    }

    /** 缓存未命中时重建结果并写回 30 分钟 TTL。 */
    @Test
    void cache_miss_rebuilds_and_writes_back_with_ttl() {
        svc().getTimeline(1L);

        verify(ops).set(eq("timeline:1"), anyString(), eq(Duration.ofMinutes(30)));
    }

    /**
     * 重要 #1：Redis 挂掉时读与写都会抛连接异常，此时时间线接口必须
     * 退化为「直接查库」，返回正确数据而不是把异常抛给调用方（否则 GET 变 500）。
     */
    @Test
    void redis_down_does_not_break_timeline_read() {
        when(ops.get(anyString())).thenThrow(new RedisConnectionFailureException("connection refused"));
        doThrow(new RedisConnectionFailureException("connection refused"))
                .when(ops).set(anyString(), anyString(), any(Duration.class));

        TimelineVO[] out = new TimelineVO[1];
        assertThatCode(() -> out[0] = svc().getTimeline(1L)).doesNotThrowAnyException();

        assertThat(out[0].getEvents()).hasSize(2);
        assertThat(out[0].getEvents().get(0).getType()).isEqualTo("LAB_TEST");
    }

    /** 缓存读取抛非连接类异常（如超时/序列化器异常）时同样回源，不向上抛。 */
    @Test
    void unexpected_redis_error_falls_back_to_rebuild() {
        when(ops.get(anyString())).thenThrow(new IllegalStateException("boom"));

        TimelineVO vo = svc().getTimeline(1L);

        assertThat(vo.getEvents()).hasSize(2);
    }

    /** 缓存内容损坏时同样回源重建，而不是把反序列化异常抛出去。 */
    @Test
    void corrupt_cache_payload_falls_back_to_rebuild() {
        when(ops.get("timeline:1")).thenReturn("{not valid json");

        TimelineVO vo = svc().getTimeline(1L);

        assertThat(vo.getEvents()).hasSize(2);
    }

    @Test
    void patient_timeline_includes_admission_and_discharge_events() {
        Admission a = new Admission();
        a.setId(9L); a.setPatientId(1L);
        a.setAdmissionTime(LocalDateTime.of(2024, 1, 1, 8, 0));
        a.setDischargeTime(LocalDateTime.of(2024, 1, 5, 10, 0));
        lenient().when(am.selectList(any())).thenReturn(List.of(a));

        TimelineVO vo = svc().getTimeline(1L);

        assertThat(vo.getEvents()).extracting(TimelineEvent::getType)
                .contains("ADMISSION", "DISCHARGE");
        // 时间升序：ADMISSION(08:00) < LAB_TEST(09:30) < VISIT(10:00) < DISCHARGE(01-05)
        assertThat(vo.getEvents().get(0).getType()).isEqualTo("ADMISSION");
        assertThat(vo.getEvents().get(vo.getEvents().size() - 1).getType()).isEqualTo("DISCHARGE");
    }

    @Test
    void admission_timeline_sorts_orders_and_vitals_ascending() {
        Admission a = new Admission(); a.setId(9L); a.setPatientId(1L);
        when(am.selectById(9L)).thenReturn(a);

        MedicalOrder o = new MedicalOrder(); o.setId(1L); o.setAdmissionId(9L);
        o.setStartTime(LocalDateTime.of(2024, 1, 1, 12, 0));
        VitalSign vs = new VitalSign(); vs.setId(1L); vs.setAdmissionId(9L);
        vs.setRecordedAt(LocalDateTime.of(2024, 1, 1, 6, 0));
        when(mom.selectList(any())).thenReturn(List.of(o));
        when(vsm.selectList(any())).thenReturn(List.of(vs));
        // mock 不解析 wrapper，setUp 里那条患者级化验单会污染住院时间轴；本用例只关心按医嘱/体征排序
        when(lm.selectList(any())).thenReturn(List.of());

        TimelineVO vo = svc().getAdmissionTimeline(9L);

        assertThat(vo.getPatientId()).isEqualTo(1L);
        assertThat(vo.getEvents()).extracting(TimelineEvent::getType)
                .containsExactly("VITAL_SIGN", "ORDER");
    }
}

package com.his.server;

import io.lettuce.core.RedisClient;
import io.lettuce.core.api.StatefulRedisConnection;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.springframework.web.client.RestTemplate;

import java.util.Arrays;

/**
 * 性能测试：{@code GET /api/patients/{id}/timeline} 的响应时间，缓存未命中（冷）与命中（热）各一轮对比。
 *
 * <p><b>需要运行中的后端（默认 localhost:8080）与 Redis</b>，因此故意不进入常规
 * {@code mvn test} 流程：JUnit 用例由系统属性 {@code test.perf.enabled=true} 显式开启，
 * 常规测试只报告 skipped，套件保持绿色。
 *
 * <p>两种执行方式（等价）：
 * <pre>
 *   # 1) JUnit（需显式开开关）
 *   mvn -pl his-server test -Dtest=PerformanceTest -Dtest.perf.enabled=true
 *   # 2) 独立 main（先用 dependency:build-classpath 导出 classpath）
 *   java -cp "target/test-classes;target/classes;&lt;cp&gt;" com.his.server.PerformanceTest
 * </pre>
 *
 * <p>可调参数（-D 传入）：{@code perf.base}、{@code perf.patientId}、{@code perf.iterations}、
 * {@code perf.warmup}、{@code perf.redis}。
 *
 * <p>两轮口径：
 * <ul>
 *   <li><b>冷（cache-miss）</b>：每轮迭代前都 {@code DEL timeline:{id}}，强制走 DB 重建，反映最坏情况。</li>
 *   <li><b>热（cache-hit）</b>：先预热写入缓存，之后不再删除，全部命中 Redis。</li>
 * </ul>
 */
public class PerformanceTest {

    private static final String BASE = System.getProperty("perf.base", "http://localhost:8080");
    private static final long PATIENT_ID = Long.getLong("perf.patientId", 1L);
    private static final int ITERATIONS = Integer.getInteger("perf.iterations", 20);
    private static final int WARMUP = Integer.getInteger("perf.warmup", 3);
    private static final String REDIS_URI = System.getProperty("perf.redis", "redis://localhost:6379");

    /** 缓存键前缀，与 TimelineServiceImpl 保持一致。 */
    private static final String CACHE_PREFIX = "timeline:";

    /**
     * Task 6 全量生成器实测输出（单位 ms）。数据生成会 TRUNCATE 六张业务表并耗时约 10 分钟，
     * 不适合在性能脚本里重跑，故直接引用该实测值；行数为事后从 MySQL 反查的生成器净产出。
     */
    private static final long BATCH_WRITE_ELAPSED_MS = 583_531L;
    /** 生成器净写入行数：patient 10000 + visit/diagnosis/prescription 各 124765 + item 179108 + lab 249251。 */
    private static final long BATCH_WRITE_ROWS = 937_419L;

    @Test
    @EnabledIfSystemProperty(named = "test.perf.enabled", matches = "true",
            disabledReason = "需要运行中的后端与 Redis；用 -Dtest.perf.enabled=true 显式开启")
    void timelineColdVsWarm() {
        run();
    }

    /** 独立入口：{@code java -cp ... com.his.server.PerformanceTest} */
    public static void main(String[] args) {
        run();
    }

    private static void run() {
        String url = BASE + "/api/patients/" + PATIENT_ID + "/timeline";
        String cacheKey = CACHE_PREFIX + PATIENT_ID;

        System.out.println("================ 性能测试：患者时间线查询 ================");
        System.out.println("目标接口 : GET " + url);
        System.out.println("迭代次数 : " + ITERATIONS + "（预热 " + WARMUP + " 次）");
        System.out.println("缓存键   : " + cacheKey);

        RestTemplate rest = new RestTemplate();

        // 探活 + 取事件数 / 响应体大小（顺带证明接口可用）
        String probe = get(rest, url);
        int eventCount = countOccurrences(probe, "\"type\":");
        System.out.println("事件数   : " + eventCount + "，响应体 " + probe.length() + " 字符");
        System.out.println();

        // 预热：建立连接、触发 JIT、把缓存填上
        for (int i = 0; i < WARMUP; i++) {
            get(rest, url);
        }

        RedisClient redisClient = RedisClient.create(REDIS_URI);
        long[] warm = new long[0];
        long[] cold = new long[0];
        try (StatefulRedisConnection<String, String> conn = redisClient.connect()) {
            // 冷轮：每次先删缓存，强制 miss 后重建
            cold = new long[ITERATIONS];
            for (int i = 0; i < ITERATIONS; i++) {
                conn.sync().del(cacheKey);
                cold[i] = timedGet(rest, url);
            }

            // 热轮：缓存已在上一轮末尾重建，直接打
            warm = new long[ITERATIONS];
            for (int i = 0; i < ITERATIONS; i++) {
                warm[i] = timedGet(rest, url);
            }

            // 结果校验：热轮结束后缓存必须存在（证明命中路径真的生效，而非参数写错）
            System.out.println("缓存校验 : EXISTS " + cacheKey + " = " + conn.sync().exists(cacheKey));
        } finally {
            redisClient.shutdown();
        }

        System.out.println();
        System.out.println("--- 缓存未命中（冷：每次 DEL 后重建）---");
        printStats(cold);
        System.out.println();
        System.out.println("--- 缓存命中（热：Redis 直接返回）---");
        printStats(warm);
        System.out.println();

        double coldAvg = avg(cold);
        double warmAvg = avg(warm);
        System.out.printf("加速比   : %.1fx（冷均 %.1fms / 热均 %.1fms）%n",
                coldAvg / warmAvg, coldAvg, warmAvg);
        System.out.println();
        System.out.println("--- 批量写入（引用 Task 6 生成器实测）---");
        System.out.printf("10000 患者 / 124765 就诊 / 六表合计 %d 行，elapsed_ms = %d（约 %.1f 分钟，≈%.0f 行/秒）%n",
                BATCH_WRITE_ROWS, BATCH_WRITE_ELAPSED_MS, BATCH_WRITE_ELAPSED_MS / 60000.0,
                BATCH_WRITE_ROWS * 1000.0 / BATCH_WRITE_ELAPSED_MS);
        System.out.println("========================================================");
    }

    /** 单次请求并计时，单位毫秒。 */
    private static long timedGet(RestTemplate rest, String url) {
        long t0 = System.nanoTime();
        String body = get(rest, url);
        long t1 = System.nanoTime();
        if (body == null || !body.contains("\"events\"")) {
            throw new IllegalStateException("响应异常，未包含 events 字段：" + body);
        }
        return (t1 - t0) / 1_000_000L;
    }

    private static String get(RestTemplate rest, String url) {
        return rest.getForObject(url, String.class);
    }

    private static void printStats(long[] samples) {
        long[] sorted = samples.clone();
        Arrays.sort(sorted);
        System.out.printf("  最小 %d ms | 中位 %d ms | 平均 %.1f ms | 最大 %d ms%n",
                sorted[0], sorted[sorted.length / 2], avg(samples), sorted[sorted.length - 1]);
        System.out.println("  样本(ms): " + Arrays.toString(samples));
    }

    private static double avg(long[] samples) {
        long sum = 0;
        for (long s : samples) {
            sum += s;
        }
        return (double) sum / samples.length;
    }

    private static int countOccurrences(String haystack, String needle) {
        int count = 0;
        int idx = 0;
        while ((idx = haystack.indexOf(needle, idx)) >= 0) {
            count++;
            idx += needle.length();
        }
        return count;
    }
}

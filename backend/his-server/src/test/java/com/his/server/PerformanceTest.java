package com.his.server;

import io.lettuce.core.RedisClient;
import io.lettuce.core.api.StatefulRedisConnection;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.springframework.web.client.RestTemplate;

import java.util.Arrays;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 性能测试：时间线查询与看板统计接口的响应时间，缓存未命中（冷）与命中（热）各一轮对比。
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
 * {@code perf.warmup}、{@code perf.dash.iterations}、{@code perf.redis}。
 *
 * <p>两轮口径：
 * <ul>
 *   <li><b>冷（cache-miss）</b>：每轮迭代前都删掉对应缓存键，强制走 DB 重算，反映最坏情况。</li>
 *   <li><b>热（cache-hit）</b>：预热写入缓存后不再删除，全部命中 Redis 直读。</li>
 * </ul>
 */
public class PerformanceTest {

    private static final String BASE = System.getProperty("perf.base", "http://localhost:8080");
    private static final long PATIENT_ID = Long.getLong("perf.patientId", 1L);
    private static final int ITERATIONS = Integer.getInteger("perf.iterations", 20);
    private static final int WARMUP = Integer.getInteger("perf.warmup", 3);
    private static final String REDIS_URI = System.getProperty("perf.redis", "redis://localhost:6379");
    /** 看板统计接口的冷热对比迭代次数。冷路径含一次全表聚合，取小一些以控制总耗时。 */
    private static final int DASH_ITERATIONS = Integer.getInteger("perf.dash.iterations", 10);

    /** 缓存键前缀，与 TimelineServiceImpl 保持一致。 */
    private static final String CACHE_PREFIX = "timeline:";
    /** 看板统计缓存键，与 DashboardServiceImpl 保持一致（设计文档第 9 节，TTL 10 分钟）。 */
    private static final String STATS_VISIT_FREQUENCY = "stats:visit-frequency";
    private static final String STATS_DRUG_DISTRIBUTION = "stats:drug-distribution";

    /**
     * 全量生成器实测输出（单位 ms）。数据生成会 TRUNCATE 六张业务表并耗时约 10 分钟，
     * 不适合在性能脚本里重跑，故直接引用该实测值；行数为事后从 MySQL 反查的生成器净产出。
     */
    private static final long BATCH_WRITE_ELAPSED_MS = 588_889L;
    /**
     * 生成器净写入行数，六表逐项相加：
     * patient 10000 + visit 125220 + diagnosis 125220 + prescription 125220
     * + prescription_item 179898 + lab_test 250930 = 816488
     * （功能测试随后追加的 1 套就诊链不计入此净产出，故引用值恒等于六表 COUNT(*) 之和）。
     */
    private static final long BATCH_WRITE_ROWS = 816_488L;

    @Test
    @EnabledIfSystemProperty(named = "test.perf.enabled", matches = "true",
            disabledReason = "需要运行中的后端与 Redis；用 -Dtest.perf.enabled=true 显式开启")
    void timelineColdVsWarm() {
        run();
    }

    /**
     * 看板三个接口的耗时：随机患者（无缓存，仅测 SQL {@code ORDER BY RAND() LIMIT 1} 的效果）
     * 与两项统计（设计文档第 9 节的 {@code stats:*} 缓存，TTL 10 分钟）的冷/热对比。
     *
     * <p>断言「热均明显小于冷均」且预热后缓存键存在——若键名写错或缓存被静默绕过，
     * 两者会接近甚至倒挂，用例即失败。
     */
    @Test
    @EnabledIfSystemProperty(named = "test.perf.enabled", matches = "true",
            disabledReason = "需要运行中的后端与 Redis；用 -Dtest.perf.enabled=true 显式开启")
    void dashboardColdVsWarm() {
        runDashboard();
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
            Long exists = conn.sync().exists(cacheKey);
            System.out.println("缓存校验 : EXISTS " + cacheKey + " = " + exists);
            assertEquals(1L, exists.longValue(),
                    "热轮结束后 " + cacheKey + " 应存在——否则「热」轮并未命中缓存，测量结论不成立");
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

        // 兜底断言：若缓存被静默绕过（例如键名写错导致「热」轮也在走 DB），
        // 冷热均值会几乎相同，此断言可让这种退化情况无法悄悄通过。
        assertTrue(coldAvg > warmAvg,
                String.format("冷路径平均耗时(%.1fms)应大于热路径(%.1fms)，缓存可能未生效", coldAvg, warmAvg));
        System.out.println();
        System.out.println("--- 批量写入（引用 Task 6 生成器实测）---");
        System.out.printf("10000 患者 / 125220 就诊 / 六表合计 %d 行，elapsed_ms = %d（约 %.1f 分钟，≈%.0f 行/秒）%n",
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

    // ------------------------------------------------------------------ 看板接口

    private static void runDashboard() {
        System.out.println("================ 性能测试：看板接口 ================");
        System.out.println("目标接口 : GET " + BASE + "/api/dashboard/...");
        System.out.println("迭代次数 : " + DASH_ITERATIONS + " 冷 + " + DASH_ITERATIONS + " 热（预热 " + WARMUP + " 次）");
        System.out.println();

        RestTemplate rest = new RestTemplate();
        RedisClient redisClient = RedisClient.create(REDIS_URI);
        try (StatefulRedisConnection<String, String> conn = redisClient.connect()) {

            // 随机患者：本接口无缓存，改动的意义在 SQL 侧（ORDER BY RAND() LIMIT 1 取代全表载入），
            // 因此只测一遍绝对值，不做冷热对比。
            long[] random = bench(rest, BASE + "/api/dashboard/random-patient", "\"name\"", conn, null, false);
            System.out.println("--- GET /api/dashboard/random-patient（无缓存，SQL 随机取 id）---");
            printStats(random);
            System.out.println();

            long[] vfCold = bench(rest, BASE + "/api/dashboard/stats/visit-frequency", "\"date\"",
                    conn, STATS_VISIT_FREQUENCY, true);
            long[] vfWarm = bench(rest, BASE + "/api/dashboard/stats/visit-frequency", "\"date\"",
                    conn, STATS_VISIT_FREQUENCY, false);
            System.out.println("--- GET /api/dashboard/stats/visit-frequency ---");
            System.out.println("  冷（每次 DEL " + STATS_VISIT_FREQUENCY + "，走 SQL GROUP BY）");
            printStats(vfCold);
            System.out.println("  热（命中 Redis 缓存）");
            printStats(vfWarm);
            System.out.println();

            long[] ddCold = bench(rest, BASE + "/api/dashboard/stats/drug-distribution", "\"drugName\"",
                    conn, STATS_DRUG_DISTRIBUTION, true);
            long[] ddWarm = bench(rest, BASE + "/api/dashboard/stats/drug-distribution", "\"drugName\"",
                    conn, STATS_DRUG_DISTRIBUTION, false);
            System.out.println("--- GET /api/dashboard/stats/drug-distribution ---");
            System.out.println("  冷（每次 DEL " + STATS_DRUG_DISTRIBUTION + "，走 SQL GROUP BY + JOIN）");
            printStats(ddCold);
            System.out.println("  热（命中 Redis 缓存）");
            printStats(ddWarm);
            System.out.println();

            System.out.printf("加速比   : 就诊频率 %.1fx（冷均 %.1fms / 热均 %.1fms）%n",
                    avg(vfCold) / avg(vfWarm), avg(vfCold), avg(vfWarm));
            System.out.printf("          用药分布 %.1fx（冷均 %.1fms / 热均 %.1fms）%n",
                    avg(ddCold) / avg(ddWarm), avg(ddCold), avg(ddWarm));
            System.out.printf("          随机患者 冷/热均 %.1fms（无缓存，作为对照组）%n", avg(random));

            // 缓存校验：热轮结束后两个键都必须存在，否则「热」轮并未命中缓存，结论不成立
            for (String key : new String[]{STATS_VISIT_FREQUENCY, STATS_DRUG_DISTRIBUTION}) {
                Long exists = conn.sync().exists(key);
                System.out.println("缓存校验 : EXISTS " + key + " = " + exists
                        + "，TTL = " + conn.sync().ttl(key) + " s（应为 600）");
                assertEquals(1L, exists.longValue(),
                        "热轮结束后 " + key + " 应存在——否则「热」轮并未命中缓存，测量结论不成立");
            }

            // 兜底断言：缓存若被静默绕过，冷热均值会接近或倒挂
            assertTrue(avg(vfCold) > avg(vfWarm),
                    String.format("就诊频率冷均(%.1fms)应大于热均(%.1fms)，缓存可能未生效", avg(vfCold), avg(vfWarm)));
            assertTrue(avg(ddCold) > avg(ddWarm),
                    String.format("用药分布冷均(%.1fms)应大于热均(%.1fms)，缓存可能未生效", avg(ddCold), avg(ddWarm)));
        } finally {
            redisClient.shutdown();
        }
        System.out.println("==================================================");
    }

    /**
     * 采集一轮样本。
     *
     * @param marker     响应体必须包含的片段，用于证明接口真的返回了业务数据
     * @param key        缓存键；为 {@code null} 表示该接口无缓存
     * @param clearFirst 为 true 时每次都 DEL 缓存键再请求（冷轮）；false 表示预热后直接打（热轮）
     */
    private static long[] bench(RestTemplate rest, String url, String marker,
                                StatefulRedisConnection<String, String> conn, String key,
                                boolean clearFirst) {
        for (int i = 0; i < WARMUP; i++) {
            get(rest, url);
        }
        if (key != null && clearFirst) {
            conn.sync().del(key);   // 确保确实从冷态开始
        }
        long[] samples = new long[DASH_ITERATIONS];
        for (int i = 0; i < DASH_ITERATIONS; i++) {
            if (key != null && clearFirst) {
                conn.sync().del(key);
            }
            samples[i] = timedGet(rest, url, marker);
        }
        return samples;
    }

    /** 单次请求并计时，单位毫秒；marker 用于校验响应体确实是该接口的业务数据。 */
    private static long timedGet(RestTemplate rest, String url, String marker) {
        long t0 = System.nanoTime();
        String body = get(rest, url);
        long t1 = System.nanoTime();
        if (body == null || !body.contains(marker)) {
            throw new IllegalStateException("响应异常，未包含 " + marker + " 字段：" + body);
        }
        return (t1 - t0) / 1_000_000L;
    }

    private static String get(RestTemplate rest, String url) {
        return rest.getForObject(url, String.class);
    }

    private static void printStats(long[] samples) {
        long[] sorted = samples.clone();
        Arrays.sort(sorted);
        System.out.printf("  最小 %d ms | 中位 %.1f ms | 平均 %.1f ms | 最大 %d ms%n",
                sorted[0], median(sorted), avg(samples), sorted[sorted.length - 1]);
        System.out.println("  样本(ms): " + Arrays.toString(samples));
    }

    /** 中位数：偶数个样本取中间两个的均值，避免偏向一侧（N=20 时不是取第 11 个）。 */
    private static double median(long[] sorted) {
        int n = sorted.length;
        if (n % 2 == 1) {
            return sorted[n / 2];
        }
        return (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0;
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

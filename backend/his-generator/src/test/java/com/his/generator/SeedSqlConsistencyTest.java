package com.his.generator;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 种子 SQL 的跨表一致性校验。
 *
 * 只读 backend/sql/*.sql 文本，不连数据库——所以它在离线环境、在任何人的机器上
 * 跑出来的结论都一样，不需要先建库。
 *
 * 断言的医学含义见 docs/superpowers/plans/2026-09-21-medical-realism.md Task 3~8。
 * 这里守的是「数据本身不矛盾」；「生成出来的数据像不像真的」由本体校验器（Task 11）守。
 */
class SeedSqlConsistencyTest {

    /** surefire 的工作目录是 backend/his-generator。 */
    private static final Path SQL_DIR = Paths.get("..", "sql");

    private static final String[] FILES = {"seed.sql", "seed-extension.sql", "seed-ontology.sql"};

    /** 文件 → 表名 → 行（列名 → 已去引号的字面量文本）。 */
    private static final Map<String, Map<String, List<Map<String, String>>>> TABLES = new LinkedHashMap<>();

    @BeforeAll
    static void loadAll() throws IOException {
        for (String file : FILES) {
            Path p = SQL_DIR.resolve(file);
            assertTrue(Files.exists(p), "缺少种子文件: " + p.toAbsolutePath());
            Map<String, List<Map<String, String>>> tables = new LinkedHashMap<>();
            parseSql(Files.readString(p, StandardCharsets.UTF_8), tables);
            TABLES.put(file, tables);
        }
    }

    // ---------- 解析器 ----------

    /** 去掉 `--` 行尾注释后，逐条 INSERT 解析成行。 */
    static void parseSql(String sql, Map<String, List<Map<String, String>>> out) {
        String src = stripLineComments(sql);
        Matcher m = Pattern.compile("(?i)INSERT\\s+INTO\\s+(\\w+)\\s*\\(([^)]*)\\)\\s*VALUES").matcher(src);
        int from = 0;
        while (m.find(from)) {
            String table = m.group(1);
            List<String> cols = Arrays.stream(m.group(2).split(","))
                    .map(String::trim).collect(Collectors.toList());
            List<Map<String, String>> rows = out.computeIfAbsent(table, k -> new ArrayList<>());
            int i = m.end();
            while (true) {
                i = skipToNextTuple(src, i);
                if (i < 0) break;
                int[] cursor = {i};
                List<String> vals = readTuple(src, cursor);
                Map<String, String> row = new LinkedHashMap<>();
                for (int c = 0; c < cols.size(); c++) {
                    row.put(cols.get(c), c < vals.size() ? vals.get(c) : null);
                }
                rows.add(row);
                i = cursor[0];
                if (i < src.length() && src.charAt(i) == ';') break;
            }
            from = i;
        }
    }

    /** 跳到下一个元组的左括号；遇到分号返回 -1。 */
    static int skipToNextTuple(String src, int i) {
        while (i < src.length()) {
            char c = src.charAt(i);
            if (c == ';') return -1;
            if (c == '(') return i;
            i++;
        }
        return -1;
    }

    /**
     * 从 src[cursor[0]]=='(' 读出一个元组，返回去引号后的字段值，并把 cursor 推过右括号。
     *
     * 必须感知单引号：种子数据里有 JSON（clinical_pathway.steps）和
     * 形如 `组[G-CBC](WBC=0.200)` 的文本，其中都可能带逗号与括号。
     */
    static List<String> readTuple(String src, int[] cursor) {
        int i = cursor[0] + 1;
        List<String> vals = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean inStr = false;
        while (i < src.length()) {
            char c = src.charAt(i);
            if (inStr) {
                if (c == '\'') {
                    // SQL 里 '' 是一个转义的单引号
                    if (i + 1 < src.length() && src.charAt(i + 1) == '\'') {
                        cur.append('\''); i += 2; continue;
                    }
                    inStr = false; i++; continue;
                }
                cur.append(c); i++; continue;
            }
            if (c == '\'') { inStr = true; i++; continue; }
            if (c == ',') { vals.add(cur.toString().trim()); cur.setLength(0); i++; continue; }
            if (c == ')') { vals.add(cur.toString().trim()); cursor[0] = i + 1; return vals; }
            cur.append(c); i++;
        }
        throw new IllegalStateException("SQL 元组未闭合（检查种子文件是否被截断）");
    }

    /** 只处理行尾 `--`；种子文件里没有字符串内含 `--` 的情况（中文字段不会有 ASCII 双连字符）。 */
    static String stripLineComments(String sql) {
        StringBuilder sb = new StringBuilder();
        for (String line : sql.split("\r?\n", -1)) {
            int p = line.indexOf("--");
            sb.append(p >= 0 ? line.substring(0, p) : line).append('\n');
        }
        return sb.toString();
    }

    // ---------- 取值辅助 ----------

    static List<Map<String, String>> rows(String file, String table) {
        return TABLES.getOrDefault(file, Map.of()).getOrDefault(table, List.of());
    }

    static long num(Map<String, String> row, String col) {
        String v = row.get(col);
        assertNotNull(v, "列 " + col + " 为空: " + row);
        return Long.parseLong(v.trim());
    }

    static BigDecimal dec(Map<String, String> row, String col) {
        return new BigDecimal(row.get(col).trim());
    }

    static boolean isNull(Map<String, String> row, String col) {
        String v = row.get(col);
        return v == null || "NULL".equalsIgnoreCase(v.trim());
    }

    /** 三份文件里某张表的全部行，按文件顺序拼接。 */
    static List<Map<String, String>> allRows(String table) {
        List<Map<String, String>> out = new ArrayList<>();
        for (String f : FILES) out.addAll(rows(f, table));
        return out;
    }

    /**
     * drug 表的 id → name。
     *
     * seed.sql 的 12 行**没有写 id 列**，靠 AUTO_INCREMENT 得到 1~12——这正是
     * Task 3 要求「seed.sql 必须先跑、且 seed-ontology.sql 的 id 从 13 起显式给出」的原因。
     * 本方法把这个约定显式编码，谁破坏了顺序，这里的断言会先炸。
     */
    static Map<Long, String> drugIdToName() {
        Map<Long, String> map = new LinkedHashMap<>();
        long auto = 1;
        for (Map<String, String> r : rows("seed.sql", "drug")) map.put(auto++, r.get("name"));
        for (Map<String, String> r : rows("seed-ontology.sql", "drug")) {
            assertFalse(isNull(r, "id"),
                    "seed-ontology.sql 的 drug 行必须显式给出 id，否则诊断映射会指错药: " + r.get("name"));
            map.put(num(r, "id"), r.get("name"));
        }
        return map;
    }

    // ---------- 断言（本步先写前 4 个，Step 3 再补齐） ----------

    @Test
    @DisplayName("药品字典达到 spec §9.2 要求的 150 种以上")
    void drugCatalogIsLargeEnough() {
        Map<Long, String> drugs = drugIdToName();
        assertTrue(drugs.size() >= 150, "药品数 " + drugs.size() + " 少于 150");
    }

    @Test
    @DisplayName("dept_diagnosis 共 72 条，六个科室各 8~15 条")
    void deptDiagnosisShape() {
        List<Map<String, String>> rows = allRows("dept_diagnosis");
        assertEquals(72, rows.size(), "dept_diagnosis 行数");
        Map<String, Long> byDept = rows.stream().collect(
                Collectors.groupingBy(r -> r.get("department"), LinkedHashMap::new, Collectors.counting()));
        assertEquals(6, byDept.size(), "科室数: " + byDept.keySet());
        byDept.forEach((dept, n) -> assertTrue(n >= 8 && n <= 15,
                dept + " 有 " + n + " 条，超出 spec §9.2 要求的 8~15"));
    }

    @Test
    @DisplayName("diagnosis_lab 的检验项目数落在 spec §9.2 要求的 30~50")
    void labItemCountInRange() {
        Set<String> items = allRows("diagnosis_lab").stream()
                .map(r -> r.get("item_code")).collect(Collectors.toSet());
        assertTrue(items.size() >= 30 && items.size() <= 50,
                "不同检验项目数 " + items.size() + " 不在 30~50");
    }

    @Test
    @DisplayName("diagnosis_operation 的术式数落在 spec §9.2 要求的 15~25")
    void operationCountInRange() {
        Set<String> ops = allRows("diagnosis_operation").stream()
                .map(r -> r.get("operation_code")).collect(Collectors.toSet());
        assertTrue(ops.size() >= 15 && ops.size() <= 25,
                "不同术式数 " + ops.size() + " 不在 15~25");
    }

    @Test
    @DisplayName("diagnosis_drug 引用的每个 drug_id 都真实存在")
    void everyMappedDrugExists() {
        Set<Long> ids = drugIdToName().keySet();
        List<String> bad = allRows("diagnosis_drug").stream()
                .filter(r -> !ids.contains(num(r, "drug_id")))
                .map(r -> r.get("drug_id")).collect(Collectors.toList());
        assertTrue(bad.isEmpty(), "指向不存在的药: " + bad);
    }

    @Test
    @DisplayName("三张映射表的 (科室, ICD) 组合都出现在 dept_diagnosis 里")
    void mappingsAnchorOnKnownDiagnoses() {
        Set<String> known = allRows("dept_diagnosis").stream()
                .map(r -> r.get("department") + "|" + r.get("icd_code")).collect(Collectors.toSet());
        for (String table : List.of("diagnosis_drug", "diagnosis_lab", "diagnosis_operation")) {
            List<String> bad = allRows(table).stream()
                    .map(r -> r.get("department") + "|" + r.get("icd_code"))
                    .filter(k -> !known.contains(k)).distinct().collect(Collectors.toList());
            assertTrue(bad.isEmpty(), table + " 锚到了不存在的诊断: " + bad);
        }
    }

    @Test
    @DisplayName("drug_dose 覆盖 diagnosis_drug 的引用集与原 12 种药")
    void drugDoseCoversReferencedSet() {
        Set<Long> dosed = allRows("drug_dose").stream()
                .map(r -> num(r, "drug_id")).collect(Collectors.toSet());
        Set<Long> referenced = allRows("diagnosis_drug").stream()
                .map(r -> num(r, "drug_id")).collect(Collectors.toSet());
        Set<Long> missing = new TreeSet<>(referenced);
        missing.removeAll(dosed);
        assertTrue(missing.isEmpty(), "被处方却没剂量规则的药: " + missing);
        for (long id = 1; id <= 12; id++) {
            assertTrue(dosed.contains(id), "原 12 种药缺剂量规则: " + id);
        }
    }

    @Test
    @DisplayName("drug_dose 的 单次×每日次数 不超过日上限")
    void drugDoseRespectsDailyCap() {
        Map<String, Integer> times = Map.of(
                "每日1次", 1, "每日2次", 2, "每日3次", 3, "每日4次", 4, "每晚1次", 1);
        for (Map<String, String> r : allRows("drug_dose")) {
            String freq = r.get("frequency");
            assertTrue(times.containsKey(freq), "频次不在取值域: " + freq + " (" + r.get("drug_id") + ")");
            BigDecimal perDay = dec(r, "single_dose").multiply(BigDecimal.valueOf(times.get(freq)));
            assertTrue(perDay.compareTo(dec(r, "max_daily_dose")) <= 0,
                    "药 " + r.get("drug_id") + " 日剂量 " + perDay + " 超过上限 " + r.get("max_daily_dose"));
            assertTrue(num(r, "days_min") >= 1, "疗程下限小于 1 天: " + r.get("drug_id"));
            assertTrue(num(r, "days_min") <= num(r, "days_max"),
                    "疗程区间倒挂: " + r.get("drug_id"));
        }
    }

    @Test
    @DisplayName("drug_dose 与 rational_rule(rule_type=2) 的日上限一致")
    void drugDoseAgreesWithRationalRule() {
        Map<Long, BigDecimal> ruleCap = new LinkedHashMap<>();
        for (Map<String, String> r : allRows("rational_rule")) {
            if (num(r, "rule_type") == 2) ruleCap.put(num(r, "drug_id"), dec(r, "max_daily_dose"));
        }
        assertFalse(ruleCap.isEmpty(), "rational_rule 里没有 rule_type=2 的剂量规则");
        ruleCap.forEach((drugId, cap) -> {
            BigDecimal doseCap = allRows("drug_dose").stream()
                    .filter(r -> num(r, "drug_id") == drugId)
                    .map(r -> dec(r, "max_daily_dose")).findFirst().orElse(null);
            assertEquals(0, cap.compareTo(doseCap),
                    "药 " + drugId + " 两张表的日上限不一致: rational_rule=" + cap + ", drug_dose=" + doseCap);
        });
    }

    @Test
    @DisplayName("【禁忌】儿科用药不含喹诺酮类，也不含阿司匹林")
    void pediatricContraindications() {
        List<Long> forbidden = List.of(26L, 27L, 28L, 29L, 9L);
        List<String> bad = allRows("diagnosis_drug").stream()
                .filter(r -> "儿科".equals(r.get("department")))
                .filter(r -> forbidden.contains(num(r, "drug_id")))
                .map(r -> r.get("icd_code") + "→" + r.get("drug_id")).collect(Collectors.toList());
        assertTrue(bad.isEmpty(), "儿科禁忌药被映射: " + bad);
    }

    @Test
    @DisplayName("【禁忌】青光眼不含抗胆碱能药")
    void glaucomaContraindication() {
        List<Long> anticholinergic = List.of(71L, 140L, 141L, 93L);
        List<String> bad = allRows("diagnosis_drug").stream()
                .filter(r -> "H40.9".equals(r.get("icd_code")))
                .filter(r -> anticholinergic.contains(num(r, "drug_id")))
                .map(r -> r.get("drug_id")).collect(Collectors.toList());
        assertTrue(bad.isEmpty(), "青光眼映射了抗胆碱能药: " + bad);
    }

    @Test
    @DisplayName("rule_config 的既有诊断全部进了 dept_diagnosis")
    void ruleConfigDiagnosesAreCovered() {
        Set<String> covered = allRows("dept_diagnosis").stream()
                .map(r -> r.get("icd_code")).collect(Collectors.toSet());
        for (Map<String, String> r : allRows("rule_config")) {
            assertTrue(covered.contains(r.get("diagnosis_code")),
                    "rule_config 的诊断 " + r.get("diagnosis_code") + " 不在 dept_diagnosis 中，"
                            + "换数据源后它会掉进 unchecked");
        }
    }

    @Test
    @DisplayName("rule_config 的每个 drug_ids 成员都在新映射里保留了")
    void ruleConfigDrugsSurviveTheSwitch() {
        Set<String> mapped = allRows("diagnosis_drug").stream()
                .map(r -> r.get("department") + "|" + r.get("icd_code") + "|" + r.get("drug_id"))
                .collect(Collectors.toSet());
        for (Map<String, String> rc : allRows("rule_config")) {
            String icd = rc.get("diagnosis_code");
            for (String drugId : rc.get("drug_ids").split(",")) {
                String want = drugId.trim();
                boolean found = allRows("dept_diagnosis").stream()
                        .filter(dd -> icd.equals(dd.get("icd_code")))
                        .anyMatch(dd -> mapped.contains(dd.get("department") + "|" + icd + "|" + want));
                assertTrue(found, "rule_config " + icd + " 的药 " + want + " 在新映射里丢了");
            }
        }
    }

    @Test
    @DisplayName("diagnosis_operation 同一 (科室,ICD) 的概率之和不超过 1")
    void operationProbabilitiesSumWithinOne() {
        Map<String, BigDecimal> sum = new LinkedHashMap<>();
        for (Map<String, String> r : allRows("diagnosis_operation")) {
            sum.merge(r.get("department") + "|" + r.get("icd_code"), dec(r, "probability"), BigDecimal::add);
        }
        sum.forEach((key, s) -> assertTrue(s.compareTo(BigDecimal.ONE) <= 0,
                key + " 的术式概率之和 " + s + " 超过 1"));
    }

    @Test
    @DisplayName("非慢病的患病率为 0，慢病不少于 10 种")
    void prevalenceOnlyOnChronicDiseases() {
        List<Map<String, String>> rows = allRows("dept_diagnosis");
        List<String> bad = rows.stream()
                .filter(r -> num(r, "chronic") == 0)
                .filter(r -> dec(r, "prevalence").compareTo(BigDecimal.ZERO) != 0)
                .map(r -> r.get("icd_code")).collect(Collectors.toList());
        assertTrue(bad.isEmpty(), "非慢病却有患病率: " + bad);
        long chronic = rows.stream().filter(r -> num(r, "chronic") == 1).count();
        assertTrue(chronic >= 10, "慢病数 " + chronic + " 少于 spec §9.2 要求的 10");
    }
}

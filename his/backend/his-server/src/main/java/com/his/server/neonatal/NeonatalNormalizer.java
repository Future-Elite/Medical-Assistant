package com.his.server.neonatal;

import java.time.*;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.regex.*;

/** Conservative normalization; original cells always remain in neonatal_source_row. */
public class NeonatalNormalizer {
    private static final Pattern DATE = Pattern.compile("^(\\d{4})[.年/-](\\d{1,2})[.月/-](\\d{1,2})日?(?:\\s+(\\d{1,2})[:：.](\\d{1,2})(?::(\\d{1,2}))?)?$");
    private static final Pattern GA = Pattern.compile("^(\\d{1,2})(?:周)?(?:\\s*\\+\\s*(\\d)(?:天)?)?$");
    public record Issue(String code, String field, String message) { }
    public record Normalized(String hospitalNo, String gender, Integer ageDays, Integer weeks, Integer days,
                             LocalDateTime admission, LocalDateTime discharge, Integer stay,
                             String outcome, List<Issue> issues) { }

    public static LocalDateTime date(String raw) {
        if (raw == null || raw.isBlank()) return null;
        Matcher m = DATE.matcher(raw.strip());
        if (!m.matches()) return null; // Never guess malformed years or an unspecified morning/afternoon time.
        try {
            return LocalDateTime.of(Integer.parseInt(m.group(1)), Integer.parseInt(m.group(2)), Integer.parseInt(m.group(3)),
                    m.group(4) == null ? 0 : Integer.parseInt(m.group(4)), m.group(5) == null ? 0 : Integer.parseInt(m.group(5)),
                    m.group(6) == null ? 0 : Integer.parseInt(m.group(6)));
        } catch (DateTimeException e) { return null; }
    }

    public static String outcome(String raw) {
        String s = raw == null ? "" : raw.strip();
        if (s.isEmpty()) return "未记录";
        // Broad parenthesized dictionaries are ambiguous, not confirmed individual dispositions.
        if (s.contains("包括") || s.contains("，") || s.contains("、")) return "其他/待核对";
        if (s.contains("死亡")) return "死亡";
        if (s.contains("自动出院")) return "自动出院";
        if (s.contains("转院")) return "转院";
        if (s.equals("治愈") || s.equals("愈")) return "治愈";
        if (s.equals("好转")) return "好转";
        if (s.startsWith("未愈")) return "未愈";
        return "其他/待核对";
    }

    public static Normalized normalize(Map<String, String> row, boolean repeatedHospitalNo) {
        List<Issue> issues = new ArrayList<>();
        String hospital = row.getOrDefault("住院号", "").strip();
        if (hospital.isEmpty()) issues.add(new Issue("MISSING_HOSPITAL_NO", "住院号", "住院号缺失；以来源行区分记录"));
        else if (repeatedHospitalNo) issues.add(new Issue("REPEATED_HOSPITAL_NO", "住院号", "同批次住院号重复，保留各条小结，未自动合并患儿"));
        String gender = row.getOrDefault("性别", "").strip();
        if (!Set.of("男", "女").contains(gender)) issues.add(new Issue("INVALID_GENDER", "性别", "性别未记录或无法识别"));
        String age = row.getOrDefault("年龄", "").strip();
        Matcher a = Pattern.compile("^(\\d+)\\s*天$").matcher(age);
        Integer ageDays = a.matches() ? Integer.valueOf(a.group(1)) : null;
        if (ageDays == null) issues.add(new Issue("INVALID_AGE", "年龄", "年龄缺失或单位不明；不推算出生日期"));
        Integer weeks = null, days = null;
        Matcher ga = GA.matcher(row.getOrDefault("孕周", "").strip());
        if (ga.matches()) {
            int w = Integer.parseInt(ga.group(1));
            Integer d = ga.group(2) == null ? null : Integer.valueOf(ga.group(2));
            if (w > 0 && w < 50 && (d == null || d < 7)) { weeks = w; days = d; }
        }
        if (weeks == null) issues.add(new Issue("UNKNOWN_GESTATION", "孕周", "孕周未提及或无法解析；不从病史推测"));
        LocalDateTime admission = date(row.get("入院时间")), discharge = date(row.get("出院时间"));
        for (String field : List.of("入院时间", "出院时间")) {
            if ((field.equals("入院时间") ? admission : discharge) == null)
                issues.add(new Issue("INVALID_DATE", field, row.getOrDefault(field, "").isBlank() ? "日期缺失" : "日期格式或时段不明确，保留原文待核对"));
        }
        Integer stay = null;
        if (admission != null && discharge != null) {
            // Date-only source values have no time precision. Compare calendar dates.
            if (discharge.toLocalDate().isBefore(admission.toLocalDate()))
                issues.add(new Issue("REVERSED_DATES", "出院时间", "出院日期早于入院日期，住院天数不参与统计"));
            else stay = Math.toIntExact(ChronoUnit.DAYS.between(admission.toLocalDate(), discharge.toLocalDate()));
        }
        for (String f : List.of("入院诊断", "出院诊断", "治疗结果"))
            if (row.getOrDefault(f, "").isBlank()) issues.add(new Issue("MISSING_FIELD", f, f + "缺失"));
        for (var e : row.entrySet()) if (e.getValue().length() >= 32767)
            issues.add(new Issue("EXCEL_TEXT_LIMIT", e.getKey(), "原单元格达到 Excel 32767 字符上限，可能已在源文件中截断"));
        return new Normalized(hospital, gender, ageDays, weeks, days, admission, discharge, stay,
                outcome(row.get("治疗结果")), issues);
    }
}

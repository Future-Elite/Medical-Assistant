package com.his.server.service.validation;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.RationalViolation;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import com.his.common.ontology.OntologyRules;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 合理用药校验：适应症 / 相互作用 / 剂量上限。
 *
 * <p>三类检查的数据源都来自医学本体表，不读自由文本、不依赖 rule_config。
 * <p>无法判定的记录一律计入 {@code unchecked} 显式报出，**不得静默跳过伪装成通过**。
 */
@Component
@RequiredArgsConstructor
public class RationalDrugValidator {

    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final DiagnosisMapper diagnosisMapper;
    /** 就诊科室来源。diagnosis 表没有科室列，只能从 visit.department 取。 */
    private final VisitMapper visitMapper;
    /** 适应症数据源：诊断→药品本体（取代原先只有 8 条诊断的 rule_config）。 */
    private final DiagnosisDrugMapper diagnosisDrugMapper;
    private final RationalRuleMapper rationalRuleMapper;
    /** 剂量数据源：药品剂量规则；与 rational_rule.rule_type=2 的取值由一致性测试保持相等。 */
    private final DrugDoseMapper drugDoseMapper;

    public RationalDrugReport validate() {
        List<Prescription> prescriptions = prescriptionMapper.selectList(null);
        RationalDrugReport report = new RationalDrugReport();
        report.setTotalPrescriptions(prescriptions.size());
        report.setViolations(new ArrayList<>());
        if (prescriptions.isEmpty()) {
            report.setRationalRate(1.0);
            return report;
        }

        // 适应症本体：诊断编码 -> 该诊断的全部行（保留 department，供精确/回落匹配）
        Map<String, List<DiagnosisDrug>> ontologyByIcd = diagnosisDrugMapper.selectList(null).stream()
                .filter(dd -> dd.getIcdCode() != null && dd.getDrugId() != null)
                .collect(Collectors.groupingBy(DiagnosisDrug::getIcdCode));

        // 日剂量上限：drug_id -> drug_dose
        Map<Long, DrugDose> doseByDrug = new HashMap<>();
        for (DrugDose d : drugDoseMapper.selectList(null)) {
            if (d.getDrugId() != null) doseByDrug.put(d.getDrugId(), d);
        }

        // 相互作用：(min,max) 规范化后的药对 -> 规则
        Map<String, RationalRule> interactions = new HashMap<>();
        for (RationalRule r : rationalRuleMapper.selectList(
                new LambdaQueryWrapper<RationalRule>().eq(RationalRule::getEnabled, 1))) {
            if (r.getRuleType() != null && r.getRuleType() == 1
                    && r.getDrugAId() != null && r.getDrugBId() != null) {
                long a = Math.min(r.getDrugAId(), r.getDrugBId());
                long b = Math.max(r.getDrugAId(), r.getDrugBId());
                interactions.put(a + "-" + b, r);
            }
        }

        List<PrescriptionItem> items = itemMapper.selectList(
                new LambdaQueryWrapper<PrescriptionItem>()
                        .in(PrescriptionItem::getPrescriptionId,
                                prescriptions.stream().map(Prescription::getId).collect(Collectors.toList())));
        Map<Long, List<PrescriptionItem>> itemsByPrescription =
                items.stream().collect(Collectors.groupingBy(PrescriptionItem::getPrescriptionId));

        List<Diagnosis> diagnoses = diagnosisMapper.selectList(
                new LambdaQueryWrapper<Diagnosis>()
                        .in(Diagnosis::getVisitId,
                                prescriptions.stream().map(Prescription::getVisitId).collect(Collectors.toSet())));
        Map<Long, String> diagnosisCodeByVisit = diagnoses.stream().collect(
                Collectors.toMap(Diagnosis::getVisitId, Diagnosis::getIcdCode, (x, y) -> x));

        // 就诊科室：visit_id -> department。空串代表该次就诊没记科室，
        // 它既不会匹配到精确行、也不会匹配到 NULL 行，自然落入 unchecked。
        Map<Long, String> visitDepartment = new HashMap<>();
        for (Visit v : visitMapper.selectList(new LambdaQueryWrapper<Visit>()
                .in(Visit::getId, prescriptions.stream().map(Prescription::getVisitId)
                        .filter(Objects::nonNull).collect(Collectors.toSet())))) {
            visitDepartment.put(v.getId(), v.getDepartment() == null ? "" : v.getDepartment());
        }

        long unchecked = 0;
        Set<Long> violatingPrescriptions = new HashSet<>();
        for (Prescription p : prescriptions) {
            List<PrescriptionItem> its = itemsByPrescription.getOrDefault(p.getId(), List.of());
            if (its.isEmpty()) continue;
            List<Long> drugIds = its.stream().map(PrescriptionItem::getDrugId)
                    .filter(Objects::nonNull).distinct().toList();
            String diagCode = diagnosisCodeByVisit.get(p.getVisitId());
            String dept = visitDepartment.get(p.getVisitId());

            // 适应症：本体未覆盖该 (科室, 诊断) -> 计入 unchecked，不伪装成通过。
            // 「诊断在体中但没有该科室的行」也走这一支——那类错误由
            // OntologyConsistencyValidator 的 DEPT_DIAGNOSIS 判违规，本校验器不重复判、也不静默通过。
            List<DiagnosisDrug> applicable = OntologyRules.select(
                    ontologyByIcd.get(diagCode),
                    DiagnosisDrug::getDepartment, DiagnosisDrug::getIcdCode, dept, diagCode);
            if (applicable.isEmpty()) {
                unchecked++;
            } else {
                Set<Long> allowed = applicable.stream()
                        .map(DiagnosisDrug::getDrugId).collect(Collectors.toSet());
                for (PrescriptionItem it : its) {
                    if (it.getDrugId() != null && !allowed.contains(it.getDrugId())) {
                        report.getViolations().add(violation(p, "INDICATION", 2,
                                "药品与诊断不符：" + dept + " 的 " + diagCode
                                        + " 未关联药品 id=" + it.getDrugId()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }

            // 相互作用：与诊断是否被本体覆盖无关，照常判定
            for (int i = 0; i < drugIds.size(); i++) {
                for (int j = i + 1; j < drugIds.size(); j++) {
                    long a = Math.min(drugIds.get(i), drugIds.get(j));
                    long b = Math.max(drugIds.get(i), drugIds.get(j));
                    RationalRule r = interactions.get(a + "-" + b);
                    if (r != null) {
                        report.getViolations().add(
                                violation(p, "INTERACTION", r.getWarningLevel(), r.getMessage()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }

            // 剂量上限：按 drug_dose 的结构化字段判定，不再解析自由文本
            for (PrescriptionItem it : its) {
                if (it.getDrugId() == null) continue;
                DrugDose dd = doseByDrug.get(it.getDrugId());
                if (dd == null) continue;
                BigDecimal single = firstDecimal(it.getDosage());
                Integer times;
                try {
                    // 取值域由 OntologyRules 单点维护（生成器用的是同一个方法）
                    times = OntologyRules.timesPerDay(it.getFrequency());
                } catch (IllegalStateException e) {
                    times = null;
                }
                if (single == null || times == null) {
                    // 剂量或频次写法无法结构化识别 -> 无法判定，显式计入 unchecked
                    // （timesPerDay 对域外取值抛异常，这里接住并如实计入，不让一个坏行中断整轮校验）
                    unchecked++;
                    continue;
                }
                BigDecimal daily = single.multiply(BigDecimal.valueOf(times));
                if (dd.getMaxDailyDose() != null && daily.compareTo(dd.getMaxDailyDose()) > 0) {
                    report.getViolations().add(violation(p, "DOSE", 1,
                            "超日剂量上限：" + it.getDrugName()
                                    + " 日剂量 " + daily.stripTrailingZeros().toPlainString()
                                    + " > 上限 " + dd.getMaxDailyDose().stripTrailingZeros().toPlainString()));
                    violatingPrescriptions.add(p.getId());
                }
            }
        }

        report.setUnchecked(unchecked);
        report.setViolationPrescriptions(violatingPrescriptions.size());
        long total = report.getTotalPrescriptions();
        report.setRationalRate(total == 0 ? 1.0
                : (double) (total - violatingPrescriptions.size()) / total);
        return report;
    }

    private RationalViolation violation(Prescription p, String type, Integer level, String message) {
        RationalViolation v = new RationalViolation();
        v.setPatientId(p.getPatientId());
        v.setPrescriptionId(p.getId());
        v.setRuleType(type);
        v.setWarningLevel(level == null ? 1 : level);
        v.setMessage(message);
        return v;
    }

    /**
     * 取字符串<b>开头</b>那段数字（允许小数点），如 "1片" -> 1、"0.5片" -> 0.5、"10ml" -> 10。
     * 开头不是数字（如 "每日3次"）返回 null。
     *
     * <p>只在 {@code dosage} 上调用，绝不喂频次——频次的取值域由
     * {@link OntologyRules#timesPerDay} 单独守。
     *
     * <p><b>为什么严格只认开头</b>：宽松地从句子中间抠数字，会把「看不懂的写法」
     * 伪装成一次成功的剂量判定；严格版让它返回 null、落进 {@code unchecked}，
     * 与「判不了就说判不了」的约定一致。
     */
    static BigDecimal firstDecimal(String s) {
        if (s == null || s.isEmpty()) return null;
        int end = 0;
        boolean dot = false;
        while (end < s.length()) {
            char c = s.charAt(end);
            if (Character.isDigit(c)) {
                end++;
            } else if (c == '.' && end > 0 && !dot) {
                dot = true;
                end++;
            } else {
                break;
            }
        }
        // 只消费到一个小数点结尾（"1."）不构成数字，且 BigDecimal("1.") 会抛异常
        if (end > 0 && s.charAt(end - 1) == '.') {
            end--;
        }
        return end == 0 ? null : new BigDecimal(s.substring(0, end));
    }
}

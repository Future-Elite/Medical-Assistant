package com.his.server.service.validation;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.dto.RationalDrugReport;
import com.his.common.dto.RationalViolation;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.*;
import java.util.stream.Collectors;

@Component
@RequiredArgsConstructor
public class RationalDrugValidator {

    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final RuleConfigMapper ruleConfigMapper;
    private final RationalRuleMapper rationalRuleMapper;

    public RationalDrugReport validate() {
        List<Prescription> prescriptions = prescriptionMapper.selectList(null);
        RationalDrugReport report = new RationalDrugReport();
        report.setTotalPrescriptions(prescriptions.size());
        report.setViolations(new ArrayList<>());
        if (prescriptions.isEmpty()) {
            report.setRationalRate(1.0);
            return report;
        }

        // 适应症允许药品：诊断编码 -> 允许 drug_id 集合
        Map<String, Set<Long>> allowedByDiagnosis = new HashMap<>();
        for (RuleConfig rc : ruleConfigMapper.selectList(null)) {
            if (rc.getDrugIds() == null || rc.getDrugIds().isBlank()) continue;
            Set<Long> ids = Arrays.stream(rc.getDrugIds().split(","))
                    .map(String::trim).filter(s -> !s.isEmpty())
                    .map(Long::parseLong).collect(Collectors.toSet());
            allowedByDiagnosis.put(rc.getDiagnosisCode(), ids);
        }

        List<RationalRule> rules = rationalRuleMapper.selectList(
                new LambdaQueryWrapper<RationalRule>().eq(RationalRule::getEnabled, 1));
        // 相互作用：规范化 (min, max) -> 规则
        Map<String, RationalRule> interactions = new HashMap<>();
        Map<Long, RationalRule> doseByDrug = new HashMap<>();
        for (RationalRule r : rules) {
            if (r.getRuleType() != null && r.getRuleType() == 1
                    && r.getDrugAId() != null && r.getDrugBId() != null) {
                long a = Math.min(r.getDrugAId(), r.getDrugBId());
                long b = Math.max(r.getDrugAId(), r.getDrugBId());
                interactions.put(a + "-" + b, r);
            } else if (r.getRuleType() != null && r.getRuleType() == 2
                    && r.getDrugId() != null) {
                doseByDrug.put(r.getDrugId(), r);
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

        Set<Long> violatingPrescriptions = new HashSet<>();
        for (Prescription p : prescriptions) {
            List<PrescriptionItem> its = itemsByPrescription.getOrDefault(p.getId(), List.of());
            if (its.isEmpty()) continue;
            List<Long> drugIds = its.stream().map(PrescriptionItem::getDrugId)
                    .filter(Objects::nonNull).distinct().toList();
            String diagCode = diagnosisCodeByVisit.get(p.getVisitId());

            // 适应症
            Set<Long> allowed = diagCode == null ? null : allowedByDiagnosis.get(diagCode);
            if (allowed != null) {
                for (PrescriptionItem it : its) {
                    if (it.getDrugId() != null && !allowed.contains(it.getDrugId())) {
                        report.getViolations().add(violation(p, "INDICATION", 2,
                                "药品与诊断不符：诊断 " + diagCode + " 未关联药品 id=" + it.getDrugId()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }
            // 相互作用
            for (int i = 0; i < drugIds.size(); i++) {
                for (int j = i + 1; j < drugIds.size(); j++) {
                    long a = Math.min(drugIds.get(i), drugIds.get(j));
                    long b = Math.max(drugIds.get(i), drugIds.get(j));
                    RationalRule r = interactions.get(a + "-" + b);
                    if (r != null) {
                        report.getViolations().add(violation(p, "INTERACTION", r.getWarningLevel(), r.getMessage()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }
            // 剂量上限
            for (PrescriptionItem it : its) {
                RationalRule r = it.getDrugId() == null ? null : doseByDrug.get(it.getDrugId());
                if (r != null) {
                    int daily = parseInt(it.getDosage()) * parseInt(it.getFrequency());
                    if (daily > r.getMaxDailyDose().intValue()) {
                        report.getViolations().add(violation(p, "DOSE", r.getWarningLevel(), r.getMessage()));
                        violatingPrescriptions.add(p.getId());
                    }
                }
            }
        }

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

    /** "1片" -> 1、"2片" -> 2；无数字默认 1。 */
    static int parseInt(String s) {
        if (s == null) return 1;
        StringBuilder sb = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (Character.isDigit(c)) sb.append(c);
            else if (sb.length() > 0) break;
        }
        return sb.length() == 0 ? 1 : Integer.parseInt(sb.toString());
    }
}

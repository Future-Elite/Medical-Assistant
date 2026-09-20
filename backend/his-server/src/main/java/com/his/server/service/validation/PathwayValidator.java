package com.his.server.service.validation;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.his.common.dto.PathwayReport;
import com.his.common.dto.PathwayVariance;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.*;

@Component
@RequiredArgsConstructor
public class PathwayValidator {

    private final ClinicalPathwayMapper pathwayMapper;
    private final AdmissionMapper admissionMapper;
    private final MedicalOrderMapper orderMapper;
    private final LabTestMapper labTestMapper;
    private final ObjectMapper objectMapper;

    private record Step(int day, String action, String item, int window) {}

    public PathwayReport validate() {
        PathwayReport report = new PathwayReport();
        report.setVariances(new ArrayList<>());
        List<ClinicalPathway> pathways = pathwayMapper.selectList(
                new LambdaQueryWrapper<ClinicalPathway>().eq(ClinicalPathway::getEnabled, 1));
        if (pathways.isEmpty()) {
            report.setExecutionRate(1.0);
            return report;
        }

        long enrolled = 0, totalSteps = 0, fulfilled = 0;
        for (ClinicalPathway pathway : pathways) {
            List<Step> steps = parseSteps(pathway.getSteps());
            if (steps.isEmpty()) continue;

            List<Admission> admissions = admissionMapper.selectList(
                    new LambdaQueryWrapper<Admission>().eq(Admission::getDiagnosis, pathway.getDiagnosisName()));
            for (Admission a : admissions) {
                enrolled++;
                List<MedicalOrder> orders = orderMapper.selectList(
                        new LambdaQueryWrapper<MedicalOrder>().eq(MedicalOrder::getAdmissionId, a.getId()));
                List<LabTest> labs = labTestMapper.selectList(
                        new LambdaQueryWrapper<LabTest>().eq(LabTest::getAdmissionId, a.getId()));

                LocalDateTime prevTime = null;
                for (Step step : steps) {
                    totalSteps++;
                    LocalDateTime lower = a.getAdmissionTime().plusDays(step.day());
                    LocalDateTime upper = lower.plusHours(step.window());
                    boolean hit = switch (step.action()) {
                        case "LAB" -> labs.stream().anyMatch(l ->
                                step.item().equals(l.getItemName())
                                        && !l.getTestedAt().isBefore(lower) && !l.getTestedAt().isAfter(upper));
                        case "MED" -> orders.stream().anyMatch(o ->
                                step.item().equals(o.getItemName())
                                        && !o.getStartTime().isBefore(lower) && !o.getStartTime().isAfter(upper));
                        case "DISCHARGE" -> a.getDischargeTime() != null
                                && !a.getDischargeTime().isBefore(lower) && !a.getDischargeTime().isAfter(upper);
                        default -> true;
                    };
                    if (hit) {
                        fulfilled++;
                        prevTime = null;
                    } else {
                        report.getVariances().add(variance(a, pathway, "MISSING",
                                "第 " + step.day() + " 天步骤缺失：action=" + step.action() + " item=" + step.item()));
                    }
                }
            }
        }
        report.setEnrolledAdmissions(enrolled);
        report.setTotalSteps(totalSteps);
        report.setFulfilledSteps(fulfilled);
        report.setExecutionRate(totalSteps == 0 ? 1.0 : (double) fulfilled / totalSteps);
        return report;
    }

    private PathwayVariance variance(Admission a, ClinicalPathway p, String type, String detail) {
        PathwayVariance v = new PathwayVariance();
        v.setPatientId(a.getPatientId());
        v.setAdmissionId(a.getId());
        v.setDiagnosisName(p.getDiagnosisName());
        v.setVarianceType(type);
        v.setDetail(detail);
        return v;
    }

    private List<Step> parseSteps(String json) {
        if (json == null || json.isBlank()) return List.of();
        try {
            List<Map<String, Object>> raw = objectMapper.readValue(json,
                    new TypeReference<List<Map<String, Object>>>() {});
            List<Step> steps = new ArrayList<>();
            for (Map<String, Object> m : raw) {
                int day = ((Number) m.get("day")).intValue();
                String action = (String) m.get("action");
                String item = (String) m.getOrDefault("item", "");
                int window = ((Number) m.get("window")).intValue();
                steps.add(new Step(day, action, item, window));
            }
            return steps;
        } catch (Exception e) {
            return List.of();
        }
    }
}

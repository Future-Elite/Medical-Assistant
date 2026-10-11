package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.entity.Patient;
import com.his.server.service.DashboardService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/dashboard")
@RequiredArgsConstructor
public class DashboardController {
    private final DashboardService dashboardService;

    @GetMapping("/random-patient")
    public Result<Patient> randomPatient() { return Result.ok(dashboardService.randomPatient()); }

    @GetMapping("/stats/visit-frequency")
    public Result<List<Map<String, Object>>> visitFrequency() {
        return Result.ok(dashboardService.visitFrequency());
    }

    @GetMapping("/stats/drug-distribution")
    public Result<List<Map<String, Object>>> drugDistribution() {
        return Result.ok(dashboardService.drugDistribution());
    }

    @GetMapping("/stats/admission-ratio")
    public Result<Map<String, Object>> admissionRatio() {
        return Result.ok(dashboardService.admissionRatio());
    }

    @GetMapping("/stats/rational-drug-rate")
    public Result<Map<String, Object>> rationalDrugRate() {
        return Result.ok(dashboardService.rationalDrugRate());
    }

    @GetMapping("/stats/pathway-rate")
    public Result<Map<String, Object>> pathwayRate() {
        return Result.ok(dashboardService.pathwayRate());
    }
}

package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.Admission;
import com.his.server.service.AdmissionService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.util.List;

@RestController
@RequestMapping("/api/admissions")
@RequiredArgsConstructor
public class AdmissionController {

    private final AdmissionService admissionService;

    @PostMapping
    public Result<Admission> create(@RequestBody Admission admission) {
        admissionService.save(admission);
        return Result.ok(admission);
    }

    @PutMapping("/{id}/discharge")
    public Result<Admission> discharge(@PathVariable Long id,
                                       @RequestParam(required = false) BigDecimal totalCost) {
        return Result.ok(admissionService.discharge(id, totalCost));
    }

    @GetMapping
    public Result<List<Admission>> list(@RequestParam Long patientId) {
        return Result.ok(admissionService.list(new LambdaQueryWrapper<Admission>()
                .eq(Admission::getPatientId, patientId)
                .orderByDesc(Admission::getAdmissionTime)));
    }
}

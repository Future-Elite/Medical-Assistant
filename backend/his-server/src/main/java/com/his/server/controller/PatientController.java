package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.his.common.common.Result;
import com.his.common.dto.TimelineVO;
import com.his.common.entity.Patient;
import com.his.server.service.PatientService;
import com.his.server.service.TimelineService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/patients")
@RequiredArgsConstructor
public class PatientController {

    private final PatientService patientService;
    private final TimelineService timelineService;

    @PostMapping
    public Result<Patient> create(@RequestBody Patient patient) {
        patientService.save(patient);
        return Result.ok(patient);
    }

    @GetMapping
    public Result<Page<Patient>> list(@RequestParam(defaultValue = "1") long page,
                                      @RequestParam(defaultValue = "10") long size,
                                      @RequestParam(required = false) String keyword) {
        LambdaQueryWrapper<Patient> qw = new LambdaQueryWrapper<>();
        if (keyword != null && !keyword.isBlank()) {
            qw.like(Patient::getName, keyword)
              .or().like(Patient::getIdCard, keyword)
              .or().like(Patient::getPhone, keyword);
        }
        qw.orderByDesc(Patient::getId);
        return Result.ok(patientService.page(new Page<>(page, size), qw));
    }

    @GetMapping("/{id}")
    public Result<Patient> get(@PathVariable Long id) {
        return Result.ok(patientService.getById(id));
    }

    @GetMapping("/{id}/timeline")
    public Result<TimelineVO> timeline(@PathVariable Long id) {
        return Result.ok(timelineService.getTimeline(id));
    }
}

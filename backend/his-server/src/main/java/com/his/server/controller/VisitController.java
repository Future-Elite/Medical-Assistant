package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.Visit;
import com.his.server.service.VisitService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/visits")
@RequiredArgsConstructor
public class VisitController {

    private final VisitService visitService;

    @PostMapping
    public Result<Visit> create(@RequestBody Visit visit) {
        visitService.save(visit);
        return Result.ok(visit);
    }

    @GetMapping
    public Result<List<Visit>> list(@RequestParam Long patientId,
                                    @RequestParam(required = false) String department) {
        LambdaQueryWrapper<Visit> qw = new LambdaQueryWrapper<Visit>()
                .eq(Visit::getPatientId, patientId);
        if (department != null && !department.isBlank()) {
            qw.eq(Visit::getDepartment, department);
        }
        qw.orderByDesc(Visit::getVisitTime);
        return Result.ok(visitService.list(qw));
    }
}

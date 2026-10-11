package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.entity.Diagnosis;
import com.his.server.service.DiagnosisService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/diagnoses")
@RequiredArgsConstructor
public class DiagnosisController {

    private final DiagnosisService diagnosisService;

    @PostMapping
    public Result<Diagnosis> create(@RequestBody Diagnosis diagnosis) {
        diagnosisService.save(diagnosis);
        return Result.ok(diagnosis);
    }
}

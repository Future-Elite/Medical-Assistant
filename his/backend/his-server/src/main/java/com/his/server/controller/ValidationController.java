package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.dto.ValidationReport;
import com.his.server.service.ValidationService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/validation")
@RequiredArgsConstructor
public class ValidationController {

    private final ValidationService validationService;

    @PostMapping("/run")
    public Result<ValidationReport> run() {
        return Result.ok(validationService.run());
    }
}

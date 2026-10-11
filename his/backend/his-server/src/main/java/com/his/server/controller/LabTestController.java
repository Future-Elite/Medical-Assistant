package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.entity.LabTest;
import com.his.server.service.LabTestService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/lab-tests")
@RequiredArgsConstructor
public class LabTestController {

    private final LabTestService labTestService;

    @PostMapping
    public Result<LabTest> create(@RequestBody LabTest labTest) {
        labTestService.save(labTest);
        return Result.ok(labTest);
    }
}

package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.entity.Prescription;
import com.his.server.service.PrescriptionService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/prescriptions")
@RequiredArgsConstructor
public class PrescriptionController {

    private final PrescriptionService prescriptionService;

    @PostMapping
    public Result<Prescription> create(@RequestBody Prescription prescription) {
        return Result.ok(prescriptionService.saveWithItems(prescription, prescription.getItems()));
    }
}

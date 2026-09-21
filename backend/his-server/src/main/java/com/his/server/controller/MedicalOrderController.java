package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.MedicalOrder;
import com.his.server.service.MedicalOrderService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/orders")
@RequiredArgsConstructor
public class MedicalOrderController {

    private final MedicalOrderService medicalOrderService;

    @PostMapping
    public Result<MedicalOrder> create(@RequestBody MedicalOrder order) {
        medicalOrderService.save(order);
        return Result.ok(order);
    }

    @PutMapping("/{id}/stop")
    public Result<MedicalOrder> stop(@PathVariable Long id) {
        return Result.ok(medicalOrderService.stop(id));
    }

    @GetMapping
    public Result<List<MedicalOrder>> list(@RequestParam Long admissionId) {
        return Result.ok(medicalOrderService.list(new LambdaQueryWrapper<MedicalOrder>()
                .eq(MedicalOrder::getAdmissionId, admissionId)
                .orderByAsc(MedicalOrder::getStartTime)));
    }
}

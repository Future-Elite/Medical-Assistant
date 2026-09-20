package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.Dispense;
import com.his.server.service.DispenseService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/dispenses")
@RequiredArgsConstructor
public class DispenseController {

    private final DispenseService dispenseService;

    @PostMapping
    public Result<Dispense> create(@RequestBody Dispense dispense) {
        dispenseService.save(dispense);
        return Result.ok(dispense);
    }

    @GetMapping
    public Result<List<Dispense>> list(@RequestParam Long visitId) {
        return Result.ok(dispenseService.list(new LambdaQueryWrapper<Dispense>()
                .eq(Dispense::getVisitId, visitId)
                .orderByAsc(Dispense::getDispensedAt)));
    }
}

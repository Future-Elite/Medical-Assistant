package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.Charge;
import com.his.server.service.ChargeService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/charges")
@RequiredArgsConstructor
public class ChargeController {

    private final ChargeService chargeService;

    @PostMapping
    public Result<Charge> create(@RequestBody Charge charge) {
        chargeService.save(charge);
        return Result.ok(charge);
    }

    @GetMapping
    public Result<List<Charge>> list(@RequestParam Long visitId) {
        return Result.ok(chargeService.list(new LambdaQueryWrapper<Charge>()
                .eq(Charge::getVisitId, visitId)
                .orderByAsc(Charge::getChargedAt)));
    }
}

package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.VitalSign;
import com.his.server.service.VitalSignService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/vital-signs")
@RequiredArgsConstructor
public class VitalSignController {

    private final VitalSignService vitalSignService;

    @PostMapping
    public Result<VitalSign> create(@RequestBody VitalSign vitalSign) {
        vitalSignService.save(vitalSign);
        return Result.ok(vitalSign);
    }

    @GetMapping
    public Result<List<VitalSign>> list(@RequestParam Long admissionId) {
        return Result.ok(vitalSignService.list(new LambdaQueryWrapper<VitalSign>()
                .eq(VitalSign::getAdmissionId, admissionId)
                .orderByAsc(VitalSign::getRecordedAt)));
    }
}

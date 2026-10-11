package com.his.server.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.common.Result;
import com.his.common.entity.Doctor;
import com.his.server.service.DoctorService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/doctors")
@RequiredArgsConstructor
public class DoctorController {

    private final DoctorService doctorService;

    @GetMapping
    public Result<List<Doctor>> list() {
        return Result.ok(doctorService.list(
                new LambdaQueryWrapper<Doctor>()
                        .orderByAsc(Doctor::getDepartment)
                        .orderByAsc(Doctor::getId)));
    }
}

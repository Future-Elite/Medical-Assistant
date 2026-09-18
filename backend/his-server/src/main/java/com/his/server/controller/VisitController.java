package com.his.server.controller;

import com.his.common.common.Result;
import com.his.common.entity.Visit;
import com.his.server.service.VisitService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

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
}

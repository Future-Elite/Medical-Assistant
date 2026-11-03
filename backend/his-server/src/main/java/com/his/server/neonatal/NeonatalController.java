package com.his.server.neonatal;

import com.his.common.common.Result;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import java.io.IOException;
import java.util.*;

@RestController
@RequestMapping("/api/neonatal")
@RequiredArgsConstructor
public class NeonatalController {
    private final NeonatalService service;
    @GetMapping("/records")
    public Result<?> records(@RequestParam Map<String, String> filters) { return Result.ok(service.records(filters)); }
    @GetMapping("/records/{id}")
    public Result<?> detail(@PathVariable long id) { return Result.ok(service.detail(id)); }
    @GetMapping("/stats")
    public Result<?> stats(@RequestParam Map<String, String> filters) { return Result.ok(service.stats(filters)); }
    @GetMapping("/quality")
    public Result<?> quality(@RequestParam Map<String, String> filters) { return Result.ok(service.quality(filters)); }
    @GetMapping("/imports")
    public Result<?> imports() { return Result.ok(service.imports()); }
    @PostMapping("/imports")
    public Result<?> upload(@RequestParam("file") MultipartFile file) throws IOException {
        return Result.ok(service.importWorkbook(file.getOriginalFilename(), file.getBytes()));
    }
}

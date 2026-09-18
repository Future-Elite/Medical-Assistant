package com.his.generator;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import java.util.Map;

@Component
@RequiredArgsConstructor
public class GeneratorRunner implements CommandLineRunner {

    private final DataGeneratorService service;

    @Value("${generator.patient-count:10000}")
    private long patientCount;

    @Override
    public void run(String... args) {
        Map<String, Long> stat = service.generate(patientCount);
        System.out.println("=== 数据生成完成 ===");
        stat.forEach((k, v) -> System.out.println(k + " = " + v));
    }
}

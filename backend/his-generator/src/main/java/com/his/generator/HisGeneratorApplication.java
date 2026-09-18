package com.his.generator;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "com.his")
@MapperScan("com.his.common.mapper")
public class HisGeneratorApplication {
    public static void main(String[] args) {
        System.exit(SpringApplication.exit(
            SpringApplication.run(HisGeneratorApplication.class, args)));
    }
}

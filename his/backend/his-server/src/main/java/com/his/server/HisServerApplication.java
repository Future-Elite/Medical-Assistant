package com.his.server;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "com.his")
public class HisServerApplication {
    public static void main(String[] args) {
        SpringApplication.run(HisServerApplication.class, args);
    }
}

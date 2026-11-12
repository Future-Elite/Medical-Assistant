package com.his.server.neonatal;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import java.nio.file.*;

@Component
@Slf4j
@RequiredArgsConstructor
@ConditionalOnProperty(name="neonatal.import-on-start", havingValue="true", matchIfMissing=true)
public class NeonatalStartup implements CommandLineRunner {
    private final NeonatalService service;
    @Value("${neonatal.source-file:../2024带孕周的出院小结汇总.xlsx}")
    private String sourceFile;
    @Override
    public void run(String... args) throws Exception {
        Path path = Path.of(sourceFile);
        if (!Files.isRegularFile(path) && sourceFile.equals("../2024带孕周的出院小结汇总.xlsx"))
            path = Path.of("2024带孕周的出院小结汇总.xlsx");
        if (!Files.isRegularFile(path)) {
            log.info("未找到启动导入文件，可在新生儿数据导入页上传 Excel"); return;
        }
        var result = service.importWorkbook(path.getFileName().toString(), Files.readAllBytes(path));
        log.info("新生儿数据就绪：批次 {}，小结 {} 条，数据问题 {} 项，复用批次 {}",
                result.get("id"), result.get("recordCount"), result.get("issueCount"), result.get("reused"));
    }
}

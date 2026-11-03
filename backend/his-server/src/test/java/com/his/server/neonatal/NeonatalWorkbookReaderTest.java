package com.his.server.neonatal;

import org.junit.jupiter.api.Test;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.zip.*;
import static org.junit.jupiter.api.Assertions.*;

class NeonatalWorkbookReaderTest {
    static byte[] workbook(List<Map<String, String>> rows) throws IOException {
        return workbook(rows, NeonatalWorkbookReader.HEADERS, false);
    }
    static byte[] workbook(List<Map<String, String>> rows, List<String> headers, boolean formula) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(bytes)) {
            put(zip, "xl/workbook.xml", "<workbook xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets><sheet name=\"新生儿\" r:id=\"s1\"/></sheets></workbook>");
            put(zip, "xl/_rels/workbook.xml.rels", "<Relationships><Relationship Id=\"s1\" Target=\"worksheets/data.xml\"/></Relationships>");
            StringBuilder sheet = new StringBuilder("<worksheet><sheetData>");
            List<Map<String, String>> data = new ArrayList<>();
            Map<String, String> header = new HashMap<>(); headers.forEach(h -> header.put(h, h)); data.add(header); data.addAll(rows);
            for (int r = 0; r < data.size(); r++) {
                sheet.append("<row r=\"").append(r + 1).append("\">");
                for (int c = 0; c < headers.size(); c++) {
                    String v = data.get(r).getOrDefault(headers.get(c), "");
                    sheet.append("<c r=\"").append((char)('A' + c)).append(r + 1).append("\" t=\"inlineStr\">");
                    if (formula && r > 0 && c == 0) sheet.append("<f>1+1</f>");
                    sheet.append("<is><t>").append(v.replace("&", "&amp;").replace("<", "&lt;")).append("</t></is></c>");
                }
                sheet.append("</row>");
            }
            put(zip, "xl/worksheets/data.xml", sheet.append("</sheetData></worksheet>").toString());
        }
        return bytes.toByteArray();
    }
    private static void put(ZipOutputStream zip, String path, String value) throws IOException {
        zip.putNextEntry(new ZipEntry(path)); zip.write(value.getBytes(StandardCharsets.UTF_8)); zip.closeEntry();
    }

    @Test void preservesFullClinicalTextAndReorderedHeaders() throws Exception {
        List<String> headers = new ArrayList<>(NeonatalWorkbookReader.HEADERS); Collections.reverse(headers);
        String text = "新生儿病史\n" + "检查结果 & 原文<保留>；".repeat(2500);
        var parsed = new NeonatalWorkbookReader().read(workbook(List.of(Map.of("住院号", "TEST001", "实验室检查及主要会诊", text)), headers, false));
        assertEquals("新生儿", parsed.sheetName()); assertEquals(2, parsed.rows().getFirst().number());
        assertEquals(text, parsed.rows().getFirst().values().get("实验室检查及主要会诊"));
        assertEquals("TEST001", parsed.rows().getFirst().values().get("住院号"));
    }
    @Test void rejectsMissingOrDuplicateHeadersAndFormulas() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> new NeonatalWorkbookReader().read(workbook(List.of(Map.of()), List.of("住院号"), false)));
        List<String> headers = new ArrayList<>(NeonatalWorkbookReader.HEADERS); headers.add("孕周");
        assertThrows(IllegalArgumentException.class, () -> new NeonatalWorkbookReader().read(workbook(List.of(Map.of()), headers, false)));
        assertThrows(IllegalArgumentException.class, () -> new NeonatalWorkbookReader().read(workbook(List.of(Map.of("性别", "男")), NeonatalWorkbookReader.HEADERS, true)));
    }
    @Test void rejectsNonWorkbook() {
        assertThrows(IllegalArgumentException.class, () -> new NeonatalWorkbookReader().read("hello".getBytes(StandardCharsets.UTF_8)));
    }
}

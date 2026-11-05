package com.his.server.neonatal;

import javax.xml.stream.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.zip.*;

/** Read-only OOXML reader. No Excel execution, external entities or new runtime dependency. */
public class NeonatalWorkbookReader {
    public static final List<String> HEADERS = List.of("性别", "年龄", "住院号", "入院时间", "出院时间",
            "入院诊断", "出院诊断", "入院时主要症状及体征", "实验室检查及主要会诊", "特殊检验及重要会诊",
            "诊疗过程", "合并症", "出院时情况", "出院医嘱", "出院指导", "治疗结果", "来源文件", "孕周");
    public record Row(int number, Map<String, String> values) { }
    public record Workbook(String sheetName, List<Row> rows) { }

    private XMLStreamReader xml(byte[] bytes) throws XMLStreamException {
        XMLInputFactory f = XMLInputFactory.newFactory();
        f.setProperty(XMLInputFactory.SUPPORT_DTD, false);
        f.setProperty("javax.xml.stream.isSupportingExternalEntities", false);
        f.setXMLResolver((a, b, c, d) -> { throw new XMLStreamException("禁止外部 XML 资源"); });
        return f.createXMLStreamReader(new ByteArrayInputStream(bytes), StandardCharsets.UTF_8.name());
    }

    public Workbook read(byte[] bytes) {
        try {
            Map<String, byte[]> entries = new HashMap<>();
            int total = 0;
            try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(bytes))) {
                for (ZipEntry e; (e = zip.getNextEntry()) != null;) {
                    if (e.isDirectory()) continue;
                    byte[] data = zip.readNBytes(128 * 1024 * 1024 + 1);
                    total += data.length;
                    if (data.length > 128 * 1024 * 1024 || total > 192 * 1024 * 1024)
                        throw new IllegalArgumentException("Excel 解压内容过大");
                    if (entries.put(e.getName(), data) != null)
                        throw new IllegalArgumentException("Excel 内部文件重复");
                }
            }
            if (!entries.containsKey("xl/workbook.xml")) throw new IllegalArgumentException("请上传有效的 .xlsx 文件");
            List<String> strings = new ArrayList<>();
            if (entries.containsKey("xl/sharedStrings.xml")) {
                XMLStreamReader x = xml(entries.get("xl/sharedStrings.xml"));
                StringBuilder text = new StringBuilder();
                boolean phonetic = false;
                while (x.hasNext()) {
                    int event = x.next();
                    if (event == XMLStreamConstants.START_ELEMENT) {
                        switch (x.getLocalName()) {
                            case "si" -> text.setLength(0);
                            case "rPh" -> phonetic = true;
                            case "t" -> { String t = x.getElementText(); if (!phonetic) text.append(t); }
                        }
                    } else if (event == XMLStreamConstants.END_ELEMENT) {
                        if (x.getLocalName().equals("si")) strings.add(text.toString());
                        if (x.getLocalName().equals("rPh")) phonetic = false;
                    }
                }
                x.close();
            }
            String sheetName = null, relation = null;
            XMLStreamReader x = xml(entries.get("xl/workbook.xml"));
            while (x.hasNext()) {
                if (x.next() == XMLStreamConstants.START_ELEMENT && x.getLocalName().equals("sheet")) {
                    sheetName = x.getAttributeValue(null, "name");
                    relation = x.getAttributeValue("http://schemas.openxmlformats.org/officeDocument/2006/relationships", "id");
                    break;
                }
            }
            x.close();
            String target = null;
            x = xml(Objects.requireNonNull(entries.get("xl/_rels/workbook.xml.rels")));
            while (x.hasNext()) {
                if (x.next() == XMLStreamConstants.START_ELEMENT && x.getLocalName().equals("Relationship")
                        && Objects.equals(relation, x.getAttributeValue(null, "Id"))) {
                    if ("External".equals(x.getAttributeValue(null, "TargetMode")))
                        throw new IllegalArgumentException("不支持外部工作表");
                    target = x.getAttributeValue(null, "Target");
                }
            }
            x.close();
            if (target == null) throw new IllegalArgumentException("Excel 缺少工作表");
            String path = target.startsWith("/") ? target.substring(1) : "xl/" + target;
            if (path.contains("..") || !entries.containsKey(path)) throw new IllegalArgumentException("Excel 工作表路径无效");
            x = xml(entries.get(path));
            Map<Integer, String> header = new LinkedHashMap<>(), cells = new LinkedHashMap<>();
            List<Row> rows = new ArrayList<>();
            int rowNumber = 0, column = 0;
            String type = null;
            StringBuilder value = new StringBuilder();
            boolean headerRead = false;
            while (x.hasNext()) {
                int event = x.next();
                if (event == XMLStreamConstants.START_ELEMENT) {
                    switch (x.getLocalName()) {
                        case "row" -> { rowNumber = Integer.parseInt(x.getAttributeValue(null, "r")); cells.clear(); }
                        case "c" -> {
                            String ref = x.getAttributeValue(null, "r");
                            column = 0;
                            for (char c : ref.toCharArray()) { if (c < 'A' || c > 'Z') break; column = column * 26 + c - 'A' + 1; }
                            type = x.getAttributeValue(null, "t"); value.setLength(0);
                        }
                        case "v", "t" -> value.append(x.getElementText());
                        case "f" -> throw new IllegalArgumentException("源表包含公式，请先保存为数值后再导入");
                    }
                } else if (event == XMLStreamConstants.END_ELEMENT) {
                    if (x.getLocalName().equals("c")) {
                        String v = value.toString();
                        if ("s".equals(type) && !v.isEmpty()) v = strings.get(Integer.parseInt(v));
                        cells.put(column, v);
                    }
                    if (x.getLocalName().equals("row") && cells.values().stream().anyMatch(v -> !v.isBlank())) {
                        if (!headerRead) {
                            cells.forEach((k, v) -> header.put(k, v.strip()));
                            if (!new HashSet<>(header.values()).containsAll(HEADERS)
                                    || HEADERS.stream().anyMatch(h -> Collections.frequency(new ArrayList<>(header.values()), h) != 1))
                                throw new IllegalArgumentException("首个工作表表头必须包含且不能重复：" + String.join("、", HEADERS));
                            headerRead = true;
                        } else {
                            Map<String, String> mapped = new LinkedHashMap<>();
                            header.forEach((k, v) -> mapped.put(v, cells.getOrDefault(k, "")));
                            rows.add(new Row(rowNumber, mapped));
                            if (rows.size() > 20000) throw new IllegalArgumentException("单次最多导入 20,000 条小结");
                        }
                    }
                }
            }
            x.close();
            if (rows.isEmpty()) throw new IllegalArgumentException("Excel 中没有出院小结数据");
            return new Workbook(sheetName, rows);
        } catch (IllegalArgumentException e) { throw e; }
        catch (Exception e) { throw new IllegalArgumentException("Excel 结构损坏或格式不受支持", e); }
    }
}

package com.his.generator;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.ThreadLocalRandom;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class DataGeneratorService {

    /** JDBC batch 分块大小 */
    private static final int BATCH_SIZE = 500;
    /** 每次就诊 5-20 次 */
    private static final int MIN_VISITS = 5;
    private static final int MAX_VISITS = 20;
    /** 病史跨度 1-3 年 */
    private static final int MIN_YEARS = 1;
    private static final int MAX_YEARS = 3;
    /** 相邻两次就诊的最小间隔（分钟），保证时间严格递增 */
    private static final long MIN_VISIT_GAP_MINUTES = 30;
    /** 末次就诊之后留给诊断/处方/检验的时间（分钟），保证下游时间不越过 now */
    private static final long DOWNSTREAM_BUFFER_MINUTES = 150;
    /** 清空时保留 drug / rule_config 字典 */
    private static final List<String> DATA_TABLES =
            List.of("patient", "visit", "diagnosis", "prescription", "prescription_item", "lab_test");

    private final PatientMapper patientMapper;
    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final LabTestMapper labTestMapper;
    private final DrugMapper drugMapper;
    private final RuleConfigMapper ruleConfigMapper;
    private final JdbcTemplate jdbcTemplate;

    /** 生成前清空六张业务数据表，使重复执行等价于「重新生成」（默认开启） */
    @Value("${generator.clear-before-run:true}")
    private boolean clearBeforeRun = true;

    private Map<Long, Drug> drugsById;
    private List<MedicalData.DiagnosisRule> rules;
    private ThreadLocalRandom rnd;

    /** 生成 patientCount 名患者及各自就诊链，返回统计信息 */
    public Map<String, Long> generate(long patientCount) {
        long start = System.currentTimeMillis();
        rnd = ThreadLocalRandom.current();
        if (clearBeforeRun) {
            clearDataTables();
        }
        drugsById = drugMapper.selectList(null).stream()
                .collect(Collectors.toMap(Drug::getId, d -> d));
        rules = loadRules();

        long visits = 0, diagnoses = 0, pres = 0, items = 0, labs = 0;
        List<Patient> patientBuf = new ArrayList<>(BATCH_SIZE);
        for (long i = 0; i < patientCount; i++) {
            patientBuf.add(newPatient());
            if (patientBuf.size() >= BATCH_SIZE) {
                long[] c = flushPatients(patientBuf);
                visits += c[0]; diagnoses += c[1]; pres += c[2]; items += c[3]; labs += c[4];
            }
        }
        long[] tail = flushPatients(patientBuf);
        visits += tail[0]; diagnoses += tail[1]; pres += tail[2]; items += tail[3]; labs += tail[4];

        Map<String, Long> stat = new LinkedHashMap<>();
        stat.put("patients", patientCount);
        stat.put("visits", visits);
        stat.put("diagnoses", diagnoses);
        stat.put("prescriptions", pres);
        stat.put("prescription_items", items);
        stat.put("lab_tests", labs);
        stat.put("elapsed_ms", System.currentTimeMillis() - start);
        return stat;
    }

    /** 幂等：清空六张业务数据表（不动 drug / rule_config） */
    private void clearDataTables() {
        for (String table : DATA_TABLES) {
            jdbcTemplate.execute("TRUNCATE TABLE " + table);
        }
    }

    /**
     * 按批写入：患者 → 就诊 → (诊断, 处方) → (处方明细, 检验)。
     * 全部走 MyBatis-Plus 的 JDBC 批量接口，自增主键回填到实体，保证外键都取自真实 id。
     *
     * @return {就诊数, 诊断数, 处方数, 明细数, 检验数}
     */
    private long[] flushPatients(List<Patient> patients) {
        if (patients.isEmpty()) {
            return new long[5];
        }
        patientMapper.insert(patients);                       // 回填 patient.id

        List<Visit> visitBuf = new ArrayList<>();
        for (Patient p : patients) {
            visitBuf.addAll(newVisits(p));
        }
        visitMapper.insert(visitBuf);                         // 回填 visit.id

        List<Diagnosis> diagBuf = new ArrayList<>(visitBuf.size());
        List<PendingPrescription> presBuf = new ArrayList<>(visitBuf.size());
        for (Visit v : visitBuf) {
            // 诊断（从规则表随机选）
            MedicalData.DiagnosisRule rule = rules.get(rnd.nextInt(rules.size()));
            Diagnosis d = new Diagnosis();
            d.setPatientId(v.getPatientId()); d.setVisitId(v.getId());
            d.setIcdCode(rule.code()); d.setDiagnosisName(rule.name());
            d.setDoctor(v.getDoctor()); d.setDiagnosedAt(v.getVisitTime().plusMinutes(rnd.nextInt(5, 20)));
            diagBuf.add(d);

            // 处方（诊断对应药品，保证诊断↔用药合理）
            Prescription pr = new Prescription();
            pr.setPatientId(v.getPatientId()); pr.setVisitId(v.getId());
            pr.setDoctor(v.getDoctor()); pr.setPrescribedAt(d.getDiagnosedAt().plusMinutes(rnd.nextInt(2, 10)));
            pr.setTotalAmount(BigDecimal.ZERO);
            presBuf.add(new PendingPrescription(pr, rule));
        }
        diagnosisMapper.insert(diagBuf);
        // 先插处方、拿回自增 id，明细才能引用到真实 prescription_id
        prescriptionMapper.insert(presBuf.stream().map(PendingPrescription::prescription).toList());

        // 处方明细 + 检验（依赖已回填的 prescription.id / visit.id）
        List<PrescriptionItem> itemBuf = new ArrayList<>();
        List<LabTest> labBuf = new ArrayList<>();
        for (PendingPrescription pp : presBuf) {
            Prescription pr = pp.prescription();
            BigDecimal total = BigDecimal.ZERO;
            for (Long drugId : pickDrugIds(pp.rule())) {
                Drug drug = drugsById.get(drugId);
                if (drug == null) {
                    throw new IllegalStateException("rule_config 的诊断 " + pp.rule().code()
                            + " 引用了不存在的 drug_id=" + drugId + "，请修正种子数据");
                }
                PrescriptionItem it = new PrescriptionItem();
                it.setPrescriptionId(pr.getId());
                it.setDrugId(drug.getId()); it.setDrugName(drug.getName());
                it.setDosage(rnd.nextInt(1, 3) + "片"); it.setFrequency("每日" + rnd.nextInt(1, 3) + "次");
                it.setDays(rnd.nextInt(3, 15)); it.setQuantity(BigDecimal.valueOf(rnd.nextInt(1, 4)));
                it.setUnit(drug.getUnit());
                total = total.add(drug.getPrice().multiply(it.getQuantity()));
                itemBuf.add(it);
            }
            pr.setTotalAmount(total);
            labBuf.addAll(newLabTests(pr));
        }
        itemMapper.insert(itemBuf);
        labTestMapper.insert(labBuf);
        // 回写处方总额（批量按 id 更新）
        prescriptionMapper.updateById(presBuf.stream().map(PendingPrescription::prescription).toList());

        patients.clear();
        return new long[]{visitBuf.size(), diagBuf.size(), presBuf.size(), itemBuf.size(), labBuf.size()};
    }

    private Patient newPatient() {
        Patient p = new Patient();
        p.setName(randomName());
        p.setGender(rnd.nextInt(2));
        int age = rnd.nextInt(18, 90);
        p.setBirthDate(LocalDate.now().minusYears(age));
        p.setIdCard(String.format("110101%d%04d%03d", rnd.nextInt(1940, 2020), rnd.nextInt(10000), rnd.nextInt(1000)));
        p.setPhone("13" + String.format("%09d", rnd.nextInt(1_000_000_000)));
        p.setAddress(randomName() + "市" + randomName() + "区" + rnd.nextInt(1, 999) + "号");
        p.setCreatedAt(LocalDateTime.now());
        return p;
    }

    /**
     * 生成一名患者的就诊链：就诊次数 5-20，病史跨度 1-3 年，
     * 时间在 [windowStart, windowEnd] 内严格递增，末次就诊不晚于 now。
     */
    private List<Visit> newVisits(Patient p) {
        int visitCount = rnd.nextInt(MIN_VISITS, MAX_VISITS + 1);
        int years = rnd.nextInt(MIN_YEARS, MAX_YEARS + 1);
        LocalDateTime windowEnd = LocalDateTime.now().minusMinutes(DOWNSTREAM_BUFFER_MINUTES);
        LocalDateTime windowStart = windowEnd.minusYears(years);
        long windowMinutes = Duration.between(windowStart, windowEnd).toMinutes();

        long[] offsets = visitOffsets(visitCount, windowMinutes);
        List<Visit> visits = new ArrayList<>(visitCount);
        for (int j = 0; j < visitCount; j++) {
            Visit v = new Visit();
            v.setPatientId(p.getId());
            v.setVisitNo("V" + p.getId() + "-" + (j + 1));
            v.setDepartment(MedicalData.DEPARTMENTS[rnd.nextInt(MedicalData.DEPARTMENTS.length)]);
            v.setDoctor(MedicalData.DOCTORS[rnd.nextInt(MedicalData.DOCTORS.length)]);
            v.setVisitTime(windowStart.plusMinutes(offsets[j]));
            v.setStatus(0);
            visits.add(v);
        }
        return visits;
    }

    /**
     * 把 visitCount 次就诊铺满 [0, windowMinutes]：首末分别贴在窗口两端（跨度恰为 1-3 年），
     * 中间按槽位抖动，且强制相邻间隔 >= {@link #MIN_VISIT_GAP_MINUTES}，保证严格递增。
     */
    private long[] visitOffsets(int visitCount, long windowMinutes) {
        long[] offsets = new long[visitCount];
        if (visitCount <= 1) {
            return offsets;
        }
        long slot = windowMinutes / (visitCount - 1);
        for (int j = 1; j < visitCount - 1; j++) {
            long ideal = j * windowMinutes / (visitCount - 1);
            long jitter = (long) ((rnd.nextDouble() - 0.5) * slot);
            long lower = offsets[j - 1] + MIN_VISIT_GAP_MINUTES;
            long upper = windowMinutes - (long) (visitCount - 1 - j) * MIN_VISIT_GAP_MINUTES;
            offsets[j] = Math.max(lower, Math.min(upper, ideal + jitter));
        }
        offsets[visitCount - 1] = windowMinutes;
        return offsets;
    }

    private List<LabTest> newLabTests(Prescription pr) {
        int labCount = rnd.nextInt(1, 4);
        List<LabTest> labs = new ArrayList<>(labCount);
        for (int k = 0; k < labCount; k++) {
            MedicalData.LabItem li = MedicalData.LAB_ITEMS.get(rnd.nextInt(MedicalData.LAB_ITEMS.size()));
            LabTest lt = new LabTest();
            lt.setPatientId(pr.getPatientId()); lt.setVisitId(pr.getVisitId());
            lt.setItemCode(li.code()); lt.setItemName(li.name());
            double val = li.low() + rnd.nextDouble() * (li.high() - li.low());
            boolean abnormal = rnd.nextDouble() < 0.15;
            if (abnormal) val = li.high() + rnd.nextDouble() * li.high() * 0.3;
            lt.setResultValue(String.format("%.2f", val));
            lt.setUnit(li.unit()); lt.setReferenceRange(li.ref());
            lt.setIsAbnormal(abnormal ? 1 : 0);
            lt.setTestedAt(pr.getPrescribedAt().plusMinutes(rnd.nextInt(20, 90)));
            labs.add(lt);
        }
        return labs;
    }

    /** 从该诊断的规则药品中随机取 1-3 种 */
    private List<Long> pickDrugIds(MedicalData.DiagnosisRule rule) {
        int drugCount = rnd.nextInt(1, Math.min(3, rule.drugIds().size()) + 1);
        List<Long> chosen = new ArrayList<>(rule.drugIds());
        Collections.shuffle(chosen, rnd);
        return chosen.subList(0, drugCount);
    }

    private List<MedicalData.DiagnosisRule> loadRules() {
        List<MedicalData.DiagnosisRule> loaded = ruleConfigMapper
                .selectList(new LambdaQueryWrapper<RuleConfig>().eq(RuleConfig::getEnabled, 1))
                .stream()
                .map(r -> new MedicalData.DiagnosisRule(r.getDiagnosisCode(), r.getDiagnosisName(),
                        parseDrugIds(r.getDrugIds())))
                .filter(r -> !r.drugIds().isEmpty())
                .toList();
        if (loaded.isEmpty()) {
            throw new IllegalStateException(
                    "rule_config 中没有可用的诊断用药规则（enabled=1 且 drug_ids 非空），请先执行 backend/sql/seed.sql");
        }
        return loaded;
    }

    /** drug_ids 可为 NULL / 空串 / 逗号列表 */
    private List<Long> parseDrugIds(String drugIds) {
        if (drugIds == null || drugIds.isBlank()) {
            return List.of();
        }
        return Arrays.stream(drugIds.split(","))
                .map(String::trim)
                .filter(s -> !s.isEmpty())
                .map(Long::parseLong)
                .toList();
    }

    private String randomName() {
        return MedicalData.SURNAMES[rnd.nextInt(MedicalData.SURNAMES.length)]
             + MedicalData.GIVEN[rnd.nextInt(MedicalData.GIVEN.length)];
    }

    /** 处方与其所属诊断规则的配对（处方批量插入后按序取用） */
    private record PendingPrescription(Prescription prescription, MedicalData.DiagnosisRule rule) {}
}

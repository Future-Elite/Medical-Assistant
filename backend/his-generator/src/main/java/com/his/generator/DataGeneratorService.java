package com.his.generator;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.his.common.entity.*;
import com.his.common.mapper.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.ThreadLocalRandom;

@Service
@RequiredArgsConstructor
public class DataGeneratorService {

    private final PatientMapper patientMapper;
    private final VisitMapper visitMapper;
    private final DiagnosisMapper diagnosisMapper;
    private final PrescriptionMapper prescriptionMapper;
    private final PrescriptionItemMapper itemMapper;
    private final LabTestMapper labTestMapper;
    private final DrugMapper drugMapper;
    private final RuleConfigMapper ruleConfigMapper;

    private List<Drug> drugs;
    private List<MedicalData.DiagnosisRule> rules;
    private ThreadLocalRandom rnd;

    /** 生成 patientCount 名患者及各自就诊链，返回统计信息 */
    public Map<String, Long> generate(long patientCount) {
        long start = System.currentTimeMillis();
        rnd = ThreadLocalRandom.current();
        drugs = drugMapper.selectList(null);
        rules = loadRules();
        long visits = 0, diagnoses = 0, pres = 0, items = 0, labs = 0;
        for (long i = 0; i < patientCount; i++) {
            long[] cnt = generateOnePatient();
            visits += cnt[0]; diagnoses += cnt[1]; pres += cnt[2]; items += cnt[3]; labs += cnt[4];
        }
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

    private long[] generateOnePatient() {
        Patient p = new Patient();
        p.setName(randomName());
        p.setGender(rnd.nextInt(2));
        int age = rnd.nextInt(18, 90);
        p.setBirthDate(LocalDate.now().minusYears(age));
        p.setIdCard(String.format("110101%d%04d%03d", rnd.nextInt(1940, 2020), rnd.nextInt(10000), rnd.nextInt(1000)));
        p.setPhone("13" + String.format("%09d", rnd.nextInt(1_000_000_000)));
        p.setAddress(randomName() + "市" + randomName() + "区" + rnd.nextInt(1, 999) + "号");
        p.setCreatedAt(LocalDateTime.now());
        patientMapper.insert(p);

        int visitCount = rnd.nextInt(5, 21);            // 5-20 次就诊
        int years = rnd.nextInt(1, 4);                  // 跨度 1-3 年
        LocalDateTime base = LocalDateTime.now().minusYears(years);
        LocalDateTime lastTime = base;

        long vCount=0,dCount=0,prCount=0,itCount=0,lCount=0;
        for (int j = 0; j < visitCount; j++) {
            // 时间严格递增：每次就诊间隔 3-120 天
            lastTime = lastTime.plusDays(rnd.nextInt(3, 120)).plusMinutes(rnd.nextInt(0, 600));
            Visit v = new Visit();
            v.setPatientId(p.getId());
            v.setVisitNo("V" + p.getId() + "-" + (j + 1));
            v.setDepartment(MedicalData.DEPARTMENTS[rnd.nextInt(MedicalData.DEPARTMENTS.length)]);
            v.setDoctor(MedicalData.DOCTORS[rnd.nextInt(MedicalData.DOCTORS.length)]);
            v.setVisitTime(lastTime);
            v.setStatus(0);
            visitMapper.insert(v);
            vCount++;

            // 诊断（从规则表随机选）
            MedicalData.DiagnosisRule rule = rules.get(rnd.nextInt(rules.size()));
            Diagnosis d = new Diagnosis();
            d.setPatientId(p.getId()); d.setVisitId(v.getId());
            d.setIcdCode(rule.code()); d.setDiagnosisName(rule.name());
            d.setDoctor(v.getDoctor()); d.setDiagnosedAt(lastTime.plusMinutes(rnd.nextInt(5, 20)));
            diagnosisMapper.insert(d);
            dCount++;

            // 处方（诊断对应药品，保证诊断↔用药合理）
            Prescription pr = new Prescription();
            pr.setPatientId(p.getId()); pr.setVisitId(v.getId());
            pr.setDoctor(v.getDoctor()); pr.setPrescribedAt(d.getDiagnosedAt().plusMinutes(rnd.nextInt(2, 10)));
            pr.setTotalAmount(BigDecimal.ZERO);
            // 先插处方、拿回自增 id，明细才能引用到真实 prescription_id
            prescriptionMapper.insert(pr);
            prCount++;

            BigDecimal total = BigDecimal.ZERO;
            int drugCount = rnd.nextInt(1, Math.min(3, rule.drugIds().size()) + 1);
            List<Long> chosen = new ArrayList<>(rule.drugIds());
            Collections.shuffle(chosen, rnd);
            for (int k = 0; k < drugCount; k++) {
                Long drugId = chosen.get(k);   // 提取为局部变量：for 循环变量 k 不是 effectively final，不能进 lambda
                Drug drug = drugs.stream().filter(x -> x.getId().equals(drugId)).findFirst().orElse(null);
                if (drug == null) continue;
                PrescriptionItem it = new PrescriptionItem();
                it.setPrescriptionId(pr.getId());
                it.setDrugId(drug.getId()); it.setDrugName(drug.getName());
                it.setDosage(rnd.nextInt(1, 3) + "片"); it.setFrequency("每日" + rnd.nextInt(1, 3) + "次");
                it.setDays(rnd.nextInt(3, 15)); it.setQuantity(BigDecimal.valueOf(rnd.nextInt(1, 4)));
                it.setUnit(drug.getUnit());
                total = total.add(drug.getPrice().multiply(it.getQuantity()));
                itemMapper.insert(it);
                itCount++;
            }
            pr.setTotalAmount(total);
            prescriptionMapper.updateById(pr);

            // 检验（随机 1-3 项）
            int labCount = rnd.nextInt(1, 4);
            for (int k = 0; k < labCount; k++) {
                MedicalData.LabItem li = MedicalData.LAB_ITEMS.get(rnd.nextInt(MedicalData.LAB_ITEMS.size()));
                LabTest lt = new LabTest();
                lt.setPatientId(p.getId()); lt.setVisitId(v.getId());
                lt.setItemCode(li.code()); lt.setItemName(li.name());
                double val = li.low() + rnd.nextDouble() * (li.high() - li.low());
                boolean abnormal = rnd.nextDouble() < 0.15;
                if (abnormal) val = li.high() + rnd.nextDouble() * li.high() * 0.3;
                lt.setResultValue(String.format("%.2f", val));
                lt.setUnit(li.unit()); lt.setReferenceRange(li.ref());
                lt.setIsAbnormal(abnormal ? 1 : 0);
                lt.setTestedAt(pr.getPrescribedAt().plusMinutes(rnd.nextInt(20, 90)));
                labTestMapper.insert(lt);
                lCount++;
            }
        }
        return new long[]{vCount,dCount,prCount,itCount,lCount};
    }

    private List<MedicalData.DiagnosisRule> loadRules() {
        return ruleConfigMapper.selectList(new LambdaQueryWrapper<RuleConfig>().eq(RuleConfig::getEnabled, 1))
                .stream()
                .map(r -> new MedicalData.DiagnosisRule(r.getDiagnosisCode(), r.getDiagnosisName(),
                        Arrays.stream(r.getDrugIds().split(",")).map(String::trim).filter(s->!s.isEmpty()).map(Long::parseLong).toList()))
                .filter(r -> !r.drugIds().isEmpty())
                .toList();
    }

    private String randomName() {
        return MedicalData.SURNAMES[rnd.nextInt(MedicalData.SURNAMES.length)]
             + MedicalData.GIVEN[rnd.nextInt(MedicalData.GIVEN.length)];
    }
}

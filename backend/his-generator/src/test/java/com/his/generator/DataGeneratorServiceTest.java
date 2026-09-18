package com.his.generator;

import com.his.common.entity.*;
import com.his.common.mapper.*;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class DataGeneratorServiceTest {

    private final List<Visit> insertedVisits = new ArrayList<>();
    private final List<Diagnosis> insertedDiagnoses = new ArrayList<>();
    private final List<Prescription> insertedPrescriptions = new ArrayList<>();
    private final List<PrescriptionItem> insertedItems = new ArrayList<>();

    private DataGeneratorService buildService() {
        PatientMapper pm = mock(PatientMapper.class);
        VisitMapper vm = mock(VisitMapper.class);
        DiagnosisMapper dm = mock(DiagnosisMapper.class);
        PrescriptionMapper prm = mock(PrescriptionMapper.class);
        PrescriptionItemMapper im = mock(PrescriptionItemMapper.class);
        LabTestMapper lm = mock(LabTestMapper.class);
        DrugMapper drugM = mock(DrugMapper.class);
        RuleConfigMapper rcM = mock(RuleConfigMapper.class);

        Drug d1 = new Drug(); d1.setId(1L); d1.setName("感冒灵颗粒"); d1.setPrice(new BigDecimal("12.00")); d1.setUnit("盒");
        Drug d2 = new Drug(); d2.setId(2L); d2.setName("布洛芬"); d2.setPrice(new BigDecimal("15.80")); d2.setUnit("盒");
        when(drugM.selectList(any())).thenReturn(List.of(d1, d2));

        RuleConfig r1 = new RuleConfig(); r1.setDiagnosisCode("J06.9"); r1.setDiagnosisName("上呼吸道感染"); r1.setDrugIds("1,2"); r1.setEnabled(1);
        when(rcM.selectList(any())).thenReturn(List.of(r1));

        AtomicLong patientSeq = new AtomicLong(0);
        doAnswer(inv -> { Patient p = inv.getArgument(0); p.setId(patientSeq.incrementAndGet()); return 1; }).when(pm).insert(any(Patient.class));
        doAnswer(inv -> { Visit v = inv.getArgument(0); v.setId(System.nanoTime()); insertedVisits.add(v); return 1; }).when(vm).insert(any(Visit.class));
        doAnswer(inv -> { Diagnosis d = inv.getArgument(0); d.setId(System.nanoTime()); insertedDiagnoses.add(d); return 1; }).when(dm).insert(any(Diagnosis.class));
        doAnswer(inv -> { Prescription p = inv.getArgument(0); p.setId(System.nanoTime()); insertedPrescriptions.add(p); return 1; }).when(prm).insert(any(Prescription.class));
        doAnswer(inv -> { PrescriptionItem it = inv.getArgument(0); it.setId(System.nanoTime()); insertedItems.add(it); return 1; }).when(im).insert(any(PrescriptionItem.class));
        when(lm.insert(any(LabTest.class))).thenReturn(1);

        return new DataGeneratorService(pm, vm, dm, prm, im, lm, drugM, rcM);
    }

    @Test
    void visits_are_strictly_increasing_in_time() {
        DataGeneratorService svc = buildService();
        Map<String, Long> stat = svc.generate(3);

        assertThat(stat.get("patients")).isEqualTo(3L);
        assertThat(stat.get("visits")).isGreaterThanOrEqualTo(15L); // 3 × 最少 5 次

        Map<Long, List<Visit>> byPatient = new HashMap<>();
        for (Visit v : insertedVisits) {
            byPatient.computeIfAbsent(v.getPatientId(), k -> new ArrayList<>()).add(v);
        }
        assertThat(byPatient.keySet()).hasSize(3);
        for (List<Visit> visits : byPatient.values()) {
            for (int i = 1; i < visits.size(); i++) {
                assertThat(visits.get(i).getVisitTime()).isAfter(visits.get(i - 1).getVisitTime());
            }
        }
    }

    @Test
    void diagnoses_come_from_rule_config() {
        DataGeneratorService svc = buildService();
        svc.generate(2);

        assertThat(insertedDiagnoses).isNotEmpty();
        for (Diagnosis d : insertedDiagnoses) {
            assertThat(d.getIcdCode()).isEqualTo("J06.9");
        }
    }

    /** 处方明细必须挂到已生成的处方上，且处方金额等于明细汇总（prescription_id 不可为 null）。 */
    @Test
    void prescription_items_reference_their_prescription_with_rolled_up_total() {
        DataGeneratorService svc = buildService();
        svc.generate(2);

        assertThat(insertedPrescriptions).isNotEmpty();
        assertThat(insertedItems).isNotEmpty();

        Map<Long, BigDecimal> priceById = Map.of(1L, new BigDecimal("12.00"), 2L, new BigDecimal("15.80"));
        Map<Long, List<PrescriptionItem>> itemsByPrescription = new HashMap<>();
        for (PrescriptionItem it : insertedItems) {
            assertThat(it.getPrescriptionId()).isNotNull();          // 必须引用真实处方 id
            itemsByPrescription.computeIfAbsent(it.getPrescriptionId(), k -> new ArrayList<>()).add(it);
        }
        assertThat(itemsByPrescription.keySet())
                .containsExactlyInAnyOrderElementsOf(insertedPrescriptions.stream().map(Prescription::getId).toList());

        for (Prescription pr : insertedPrescriptions) {
            List<PrescriptionItem> its = itemsByPrescription.get(pr.getId());
            assertThat(its).isNotNull();
            BigDecimal expected = its.stream()
                    .map(it -> priceById.get(it.getDrugId()).multiply(it.getQuantity()))
                    .reduce(BigDecimal.ZERO, BigDecimal::add);
            assertThat(pr.getTotalAmount()).isEqualByComparingTo(expected);
        }
    }
}

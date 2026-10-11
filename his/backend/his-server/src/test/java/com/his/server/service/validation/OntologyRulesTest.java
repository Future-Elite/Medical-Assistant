package com.his.server.service.validation;

import com.his.common.entity.DeptDiagnosis;
import com.his.common.entity.DiagnosisLab;
import com.his.common.ontology.OntologyRules;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class OntologyRulesTest {

    private DeptDiagnosis row(Integer gender, Integer minAge, Integer maxAge) {
        DeptDiagnosis d = new DeptDiagnosis();
        d.setGender(gender);
        d.setMinAge(minAge);
        d.setMaxAge(maxAge);
        return d;
    }

    @Test
    @DisplayName("患者性别(0男1女)与本体性别(0不限1仅男2仅女)是两个域，必须映射后再比")
    void gender_domains_are_mapped_not_compared_directly() {
        // 仅女：女患者(1)适用、男患者(0)不适用
        assertThat(OntologyRules.ageGenderMatches(row(2, 0, 89), 30, 1)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(2, 0, 89), 30, 0)).isFalse();
        // 仅男：男患者(0)适用、女患者(1)不适用
        assertThat(OntologyRules.ageGenderMatches(row(1, 0, 89), 30, 0)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(1, 0, 89), 30, 1)).isFalse();
        // 不限：两者都适用
        assertThat(OntologyRules.ageGenderMatches(row(0, 0, 89), 30, 0)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(0, 0, 89), 30, 1)).isTrue();
        // 本体行为 NULL 时按「不限」处理，不得 NPE
        assertThat(OntologyRules.ageGenderMatches(row(null, 0, 89), 30, 1)).isTrue();
    }

    @Test
    @DisplayName("原始形式与实体形式判定一致：生成器展平后的 Disease 走的就是这一条")
    void primitive_overload_agrees_with_the_entity_form() {
        // 生成器的 Disease record 不能再拼一个 DeptDiagnosis 出来复用规则，
        // 于是走这个重载；两种写法必须给出同一个答案，否则生成器与校验器又会分叉。
        int[][] cases = {{1, 30, 0}, {2, 30, 1}, {1, 30, 1}, {2, 30, 0}, {0, 30, 0}, {0, 30, 1}};
        for (int[] c : cases) {
            assertThat(OntologyRules.ageGenderMatches(c[0], 0, 89, c[1], c[2]))
                    .as("本体性别=%d 年龄=%d 患者性别=%d", c[0], c[1], c[2])
                    .isEqualTo(OntologyRules.ageGenderMatches(row(c[0], 0, 89), c[1], c[2]));
        }
    }

    @Test
    @DisplayName("年龄为闭区间；minAge/maxAge 为 null 表示不设限")
    void age_range_is_inclusive_and_null_means_unbounded() {
        assertThat(OntologyRules.ageGenderMatches(row(0, 18, 65), 18, 0)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(0, 18, 65), 65, 0)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(0, 18, 65), 17, 0)).isFalse();
        assertThat(OntologyRules.ageGenderMatches(row(0, 18, 65), 66, 0)).isFalse();
        assertThat(OntologyRules.ageGenderMatches(row(0, null, null), 0, 1)).isTrue();
        assertThat(OntologyRules.ageGenderMatches(row(0, null, null), 89, 0)).isTrue();
    }

    @Test
    @DisplayName("科室优先级：精确行胜过「不限科室」的行；无精确行时才回落；键不存在返回空")
    void select_prefers_the_exact_department_row() {
        DeptDiagnosis peds = row(0, 0, 89);
        peds.setDepartment("儿科");
        peds.setIcdCode("J06.9");
        DeptDiagnosis anyDept = row(0, 0, 89);
        anyDept.setDepartment(null);
        anyDept.setIcdCode("J06.9");
        List<DeptDiagnosis> rows = List.of(peds, anyDept);

        assertThat(OntologyRules.select(rows, DeptDiagnosis::getDepartment,
                DeptDiagnosis::getIcdCode, "儿科", "J06.9")).containsExactly(peds);
        // 内科没有精确行 -> 回落到「不限科室」那一行
        assertThat(OntologyRules.select(rows, DeptDiagnosis::getDepartment,
                DeptDiagnosis::getIcdCode, "内科", "J06.9")).containsExactly(anyDept);
        // 键根本不在本体里 -> 空列表，调用方必须计入 unchecked，不得当作通过
        assertThat(OntologyRules.select(rows, DeptDiagnosis::getDepartment,
                DeptDiagnosis::getIcdCode, "内科", "Z99.9")).isEmpty();
        // 科室为 null 的就诊不能 NPE，同样返回空
        assertThat(OntologyRules.select(rows, DeptDiagnosis::getDepartment,
                DeptDiagnosis::getIcdCode, null, "J06.9")).isEmpty();
    }

    @Test
    @DisplayName("select 只在适用行里找，不跨科室误判")
    void select_looks_up_values_in_the_applicable_rows_only() {
        DiagnosisLab crp = new DiagnosisLab();
        crp.setIcdCode("J06.9");
        crp.setDepartment("儿科");
        crp.setItemCode("BLD-CRP");
        DiagnosisLab wbc = new DiagnosisLab();
        wbc.setIcdCode("J06.9");
        wbc.setDepartment("内科");
        wbc.setItemCode("BLD-WBC");
        List<DiagnosisLab> rows = List.of(crp, wbc);

        assertThat(OntologyRules.select(rows, DiagnosisLab::getDepartment, DiagnosisLab::getIcdCode,
                "儿科", "J06.9")).extracting(DiagnosisLab::getItemCode).containsExactly("BLD-CRP");
        // BLD-WBC 是内科那一行开的，儿科取适用行时不该看见它
        assertThat(OntologyRules.select(rows, DiagnosisLab::getDepartment, DiagnosisLab::getIcdCode,
                "儿科", "J06.9")).extracting(DiagnosisLab::getItemCode).doesNotContain("BLD-WBC");
    }

    @Test
    @DisplayName("频次取值域：每日1~4次/每晚1次，域外抛异常而不是猜一个默认值")
    void times_per_day_covers_exactly_the_drug_dose_domain() {
        assertThat(OntologyRules.timesPerDay("每日1次")).isEqualTo(1);
        assertThat(OntologyRules.timesPerDay("每日2次")).isEqualTo(2);
        assertThat(OntologyRules.timesPerDay("每日3次")).isEqualTo(3);
        assertThat(OntologyRules.timesPerDay("每日4次")).isEqualTo(4);
        assertThat(OntologyRules.timesPerDay("每晚1次")).isEqualTo(1);
        // 域外不得静默返回 1——那会让「校验器不认得」伪装成「剂量正常」
        assertThat(org.assertj.core.api.Assertions.catchThrowable(
                () -> OntologyRules.timesPerDay("每日5次"))).isInstanceOf(IllegalStateException.class);
        assertThat(org.assertj.core.api.Assertions.catchThrowable(
                () -> OntologyRules.timesPerDay(null))).isInstanceOf(IllegalStateException.class);
    }
}

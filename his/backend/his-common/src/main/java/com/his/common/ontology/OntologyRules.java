package com.his.common.ontology;

import com.his.common.entity.DeptDiagnosis;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Function;

/**
 * 医学本体匹配规则。**本类是这些规则在全项目的唯一实现**，
 * 校验器（his-server）与生成器（his-generator）都从这里取，不得各写一份。
 *
 * <p>放在 his-common 的原因：模块依赖是 generator → common、server → common，
 * 两者互不依赖；放 server 则 generator 用不到。本类只操作 his-common 自己的实体，
 * 不依赖 Spring，放在这里没有任何代价。
 */
public final class OntologyRules {

    private OntologyRules() {
    }

    /**
     * 科室优先级匹配：选出适用于 {@code (actualDept, actualKey)} 的本体行。
     *
     * <p>规则：先取 {@code department} 精确匹配的行；无匹配时回落到
     * {@code department == null} 的行（null 表示「该诊断不限科室」）。
     * 返回空列表表示**本体覆盖不到这个 (科室, 键) 组合**，调用方必须如实计入
     * {@code unchecked} 或报「未覆盖」，**不得当作「通过」**。覆盖不到有两种成因：
     * <ol>
     *   <li>该键（ICD 编码）根本没进本体；</li>
     *   <li>键在体中，但既没有本科室的行、也没有 NULL 的行
     *       ——即「这个科室本就不该看这个病」。</li>
     * </ol>
     * 第 2 种由 {@code OntologyConsistencyValidator} 的对应检查判违规
     * （门诊诊断走 DEPT_DIAGNOSIS，手术走 OPERATION_DIAGNOSIS），本类只负责如实回报「判不了」。
     *
     * <p>当前种子数据里三张映射表的 {@code department} 全部非空，回落分支由单元测试用
     * 合成行覆盖。**不要因为数据里没有就删掉它**——它是「一个诊断不限科室」唯一的表达方式。
     *
     * @param deptOf 取一行所属科室（可能为 null，表示不限科室）
     * @param keyOf  取一行的键（如 ICD 编码）
     */
    public static <T> List<T> select(List<T> rows,
                                     Function<T, String> deptOf,
                                     Function<T, String> keyOf,
                                     String actualDept,
                                     String actualKey) {
        if (rows == null || rows.isEmpty() || actualDept == null || actualKey == null) {
            return List.of();
        }
        List<T> sameKey = new ArrayList<>();
        for (T row : rows) {
            if (actualKey.equals(keyOf.apply(row))) {
                sameKey.add(row);
            }
        }
        if (sameKey.isEmpty()) {
            return List.of();
        }
        List<T> exact = new ArrayList<>();
        for (T row : sameKey) {
            if (actualDept.equals(deptOf.apply(row))) {
                exact.add(row);
            }
        }
        if (!exact.isEmpty()) {
            return exact;
        }
        List<T> unrestricted = new ArrayList<>();
        for (T row : sameKey) {
            if (deptOf.apply(row) == null) {
                unrestricted.add(row);
            }
        }
        return unrestricted;
    }

    /**
     * 本体行是否适用于该年龄与性别。
     *
     * <p><b>两个独立的性别取值域，绝不可直接比较：</b>
     * <table border="1">
     *   <tr><th>来源</th><th>取值</th></tr>
     *   <tr><td>{@code Patient.gender}、{@code visit_rate.gender} 的 1/2 分支</td>
     *       <td>0=男 1=女</td></tr>
     *   <tr><td>{@code DeptDiagnosis.gender}</td><td>0=不限 1=仅男 2=仅女</td></tr>
     * </table>
     * 本方法的参数 {@code gender} 收的是**患者性别（0=男 1=女）**，在方法内部映射到本体域。
     * 若图省事写成 {@code row.getGender() == gender}，则「仅女」的行对女性为假、对男性为真，
     * 男女**正好反了**——而且生成器与校验器会犯同一个错，两边一起认为数据是对的，
     * 这正是把规则收进一个类要防的事，所以映射必须写在这里、只写一次。
     *
     * <p>{@code minAge}/{@code maxAge} 为闭区间，null 表示不设限。
     * 生成器用它筛「这个患者能得的诊断」，校验器用它判违规，**必须是同一个判定**——
     * 否则生成器造的数据会被校验器判违规。
     */
    public static boolean ageGenderMatches(DeptDiagnosis row, int age, int gender) {
        return ageGenderMatches(row.getGender(), row.getMinAge(), row.getMaxAge(), age, gender);
    }

    /**
     * 同上的原始形式。供**已经把本体行展平成自己的类型**的调用方使用
     * （生成器侧的 {@code Disease} record 就是这样），
     * 免得它为了复用这条规则再拼一个 {@code DeptDiagnosis} 出来。
     */
    public static boolean ageGenderMatches(Integer rowGender, Integer minAge, Integer maxAge,
                                           int age, int gender) {
        int g = rowGender == null ? 0 : rowGender;
        // 患者域 0男/1女 → 本体域 1仅男/2仅女
        boolean genderOk = g == 0 || g == (gender == 0 ? 1 : 2);
        boolean ageOk = (minAge == null || age >= minAge) && (maxAge == null || age <= maxAge);
        return genderOk && ageOk;
    }

    /**
     * 频次文本 → 每日次数。
     *
     * <p><b>取值域</b>（逐字等于 {@code drug_dose.frequency}）：
     * {@code 每日1次} / {@code 每日2次} / {@code 每日3次} / {@code 每日4次} / {@code 每晚1次}
     * → 1 / 2 / 3 / 4 / 1。多一个键或少一个键都会让「校验器认得的频次」与
     * 「生成器写出的频次」错开——那正是 {@code parseInt("bid")} 时代错判的来源。
     *
     * <p><b>为什么收在这里而不是各写一份</b>：校验器要它算日剂量（判超量），
     * 生成器要它算疗程总量。两处各写一张 Map 的下场是生成器写得出 {@code 每日5次}
     * 而校验器不认得它，整条处方落进 {@code unchecked}，
     * 看起来像「校验器覆盖不足」，实际是生成器越过了取值域。
     *
     * @throws IllegalStateException 取值域之外——**不返回默认值**，
     *         见三处调用的共同约定：判不了就说判不了，不猜
     */
    public static int timesPerDay(String frequency) {
        if (frequency == null) {
            throw new IllegalStateException("drug_dose.frequency 为空，无法确定每日次数");
        }
        return switch (frequency.trim()) {
            case "每日1次", "每晚1次" -> 1;
            case "每日2次" -> 2;
            case "每日3次" -> 3;
            case "每日4次" -> 4;
            default -> throw new IllegalStateException(
                    "drug_dose.frequency 取值域外（只允许 每日1~4次 / 每晚1次）: " + frequency);
        };
    }
}

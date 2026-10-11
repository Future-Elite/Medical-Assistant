package com.his.common.dto;

import lombok.Data;
import java.util.List;

@Data
public class RationalDrugReport {
    private long totalPrescriptions;
    private long violationPrescriptions;
    private double rationalRate;
    private List<RationalViolation> violations;

    /**
     * 无法判定的处方数。三种成因：诊断不在本体、诊断在体中但不含本科室的行、
     * 剂量或频次写法无法结构化识别。
     *
     * <p>**它不是「不通过」，也不是「通过」——它是「没检查」。** 因此
     * {@code rationalRate} 的分母仍是全部处方：未覆盖的处方不计入违规，也就默认算「合理」，
     * 这正是必须在看板上把 {@code unchecked} 和 {@code rationalRate} 一起显示的原因。
     * 重写生成器后，本值应趋近于 0；若长期不为 0，说明本体有洞，而不是数据变好了。
     */
    private long unchecked;
}

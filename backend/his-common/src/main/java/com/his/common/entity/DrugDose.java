package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 药品剂量规则表：单次剂量 × 每日次数 ≤ max_daily_dose，单位与 rational_rule.rule_type=2 直接可比。 */
@Data
@TableName("drug_dose")
public class DrugDose {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long drugId;
    private BigDecimal singleDose;
    private String doseUnit;
    private String frequency;
    private BigDecimal maxDailyDose;
    private Integer daysMin;
    private Integer daysMax;
}

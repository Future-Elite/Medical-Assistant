package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;

@Data
@TableName("rational_rule")
public class RationalRule {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Integer ruleType;
    private String diagnosisCode;
    private Long drugAId;
    private Long drugBId;
    private Long drugId;
    private BigDecimal maxDailyDose;
    private Integer warningLevel;
    private String message;
    private Integer enabled;
}

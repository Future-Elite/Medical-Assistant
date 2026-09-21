package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 就诊率配置表：年龄段/性别 → 就诊率乘子。 */
@Data
@TableName("visit_rate")
public class VisitRate {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Integer ageMin;
    private Integer ageMax;
    private Integer gender;
    private BigDecimal multiplier;
}

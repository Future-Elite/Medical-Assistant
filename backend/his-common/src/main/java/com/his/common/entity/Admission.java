package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("admission")
public class Admission {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private String admissionNo;
    private String department;
    private String doctor;
    private String bedNo;
    private String diagnosis;
    private LocalDateTime admissionTime;
    private LocalDateTime dischargeTime;
    private BigDecimal deposit;
    private BigDecimal totalCost;
    private Integer status;
}

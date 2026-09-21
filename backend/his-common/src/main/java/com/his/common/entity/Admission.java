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
    /** OUTPATIENT门诊转入 / EMERGENCY急诊 / TRANSFER转院 */
    private String admissionRoute;
    /** CURED医嘱离院 / IMPROVED好转 / TRANSFER转院 / DEATH死亡 / AGAINST_ADVICE自动出院 */
    private String dischargeDisposition;
    private String admissionDiagnosis;
    private String dischargeDiagnosis;
    private LocalDateTime admissionTime;
    private LocalDateTime dischargeTime;
    private BigDecimal deposit;
    private BigDecimal totalCost;
    private Integer status;
}

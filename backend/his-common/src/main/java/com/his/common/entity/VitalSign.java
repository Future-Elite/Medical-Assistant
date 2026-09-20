package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("vital_sign")
public class VitalSign {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long admissionId;
    private BigDecimal temperature;
    private Integer pulse;
    private Integer respiration;
    private Integer systolicBp;
    private Integer diastolicBp;
    private LocalDateTime recordedAt;
    private String nurse;
}

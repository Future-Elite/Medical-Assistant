package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("dispense")
public class Dispense {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long visitId;
    private Long prescriptionId;
    private String drugName;
    private BigDecimal quantity;
    private LocalDateTime dispensedAt;
    private String pharmacist;
}

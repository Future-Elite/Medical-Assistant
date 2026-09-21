package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("charge")
public class Charge {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long visitId;
    private String chargeNo;
    private Integer itemType;
    private String itemName;
    private BigDecimal amount;
    private LocalDateTime chargedAt;
}

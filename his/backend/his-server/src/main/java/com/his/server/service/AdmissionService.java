package com.his.server.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.Admission;
import java.math.BigDecimal;

public interface AdmissionService extends IService<Admission> {
    /** 出院结算：填出院时间、结算总费用、置 status=1。返回 null 表示记录不存在。 */
    Admission discharge(Long id, BigDecimal totalCost);
}

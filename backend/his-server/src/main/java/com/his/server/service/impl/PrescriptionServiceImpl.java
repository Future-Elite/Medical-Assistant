package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Drug;
import com.his.common.entity.Prescription;
import com.his.common.entity.PrescriptionItem;
import com.his.common.mapper.DrugMapper;
import com.his.common.mapper.PrescriptionItemMapper;
import com.his.common.mapper.PrescriptionMapper;
import com.his.server.service.PrescriptionService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;

@Service
@RequiredArgsConstructor
public class PrescriptionServiceImpl extends ServiceImpl<PrescriptionMapper, Prescription>
        implements PrescriptionService {

    private final PrescriptionItemMapper itemMapper;
    private final DrugMapper drugMapper;

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Prescription saveWithItems(Prescription prescription, List<PrescriptionItem> items) {
        BigDecimal total = items.stream()
                .map(i -> {
                    BigDecimal price = BigDecimal.ZERO;
                    if (i.getDrugId() != null) {
                        Drug drug = drugMapper.selectById(i.getDrugId());
                        if (drug != null && drug.getPrice() != null) price = drug.getPrice();
                    }
                    return price.multiply(i.getQuantity() == null ? BigDecimal.ZERO : i.getQuantity());
                })
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        prescription.setTotalAmount(total);
        save(prescription);
        items.forEach(i -> i.setPrescriptionId(prescription.getId()));
        items.forEach(itemMapper::insert);
        return prescription;
    }
}

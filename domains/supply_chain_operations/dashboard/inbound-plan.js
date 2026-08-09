window.SUPPLY_CHAIN_INBOUND_PLAN = {
  capturedAt: "2026-08-09T15:30:00+08:00",
  source: "seaya_oms_inbound_managein_logged_in_readonly",
  regions: {
    MY: {
      totalUnits: 1000,
      allocationPolicy: "SINGLE_ACTIVE_BATCH",
      batches: [{
        batchId: "MYSH4038-59560",
        totalUnits: 1000,
        anchorDate: "2026-08-07",
        anchorType: "MARKED_SHIPPED",
        transportDays: 25,
        shelvingDays: 2,
        estimatedSellableDate: "2026-09-03",
        confidence: "ESTIMATED"
      }]
    },
    TH: {
      totalUnits: 3350,
      allocationPolicy: "EXACT_BATCH_SKU_REQUIRED",
      batches: [{
        batchId: "THML4038-58701",
        totalUnits: 2100,
        anchorDate: "2026-07-29",
        anchorType: "CREATED_FALLBACK",
        transportDays: 15,
        shelvingDays: 2,
        estimatedSellableDate: "2026-08-15",
        confidence: "ESTIMATED",
        skuQuantities: {"0021": null}
      }, {
        batchId: "THSL4038-59557",
        totalUnits: 1250,
        anchorDate: "2026-08-07",
        anchorType: "CREATED_FALLBACK",
        transportDays: 15,
        shelvingDays: 2,
        estimatedSellableDate: "2026-08-24",
        confidence: "ESTIMATED",
        skuQuantities: {"0021": null}
      }]
    },
    VN: {
      totalUnits: 330,
      allocationPolicy: "SINGLE_ACTIVE_BATCH",
      batches: [{
        batchId: "VNML4038-59508",
        totalUnits: 330,
        anchorDate: "2026-08-07",
        anchorType: "CREATED_FALLBACK",
        transportDays: 15,
        shelvingDays: 2,
        estimatedSellableDate: "2026-08-24",
        confidence: "ESTIMATED"
      }]
    },
    PH: {
      totalUnits: 510,
      allocationPolicy: "SINGLE_ACTIVE_BATCH",
      batches: [{
        batchId: "PHPH4038-59553",
        totalUnits: 510,
        anchorDate: "2026-08-07",
        anchorType: "CREATED_FALLBACK",
        transportDays: 25,
        shelvingDays: 2,
        estimatedSellableDate: "2026-09-03",
        confidence: "ESTIMATED"
      }]
    }
  }
};

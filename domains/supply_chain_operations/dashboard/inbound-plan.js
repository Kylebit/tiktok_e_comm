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
        createdAt: "2026-08-07T16:54:05+08:00",
        anchorDate: null,
        anchorAt: null,
        anchorType: "REACHED_DOMESTIC_WAREHOUSE_REQUIRED",
        transportDays: 25,
        shelvingDays: 2,
        estimatedSellableDate: null,
        confidence: "BLOCKED_ANCHOR"
      }]
    },
    TH: {
      totalUnits: 3350,
      allocationPolicy: "EXACT_BATCH_SKU_REQUIRED",
      batches: [{
        batchId: "THML4038-58701",
        totalUnits: 2100,
        createdAt: "2026-07-29T09:20:43+08:00",
        anchorDate: "2026-08-04",
        anchorAt: "2026-08-04T15:39:15+08:00",
        anchorType: "REACHED_DOMESTIC_WAREHOUSE",
        transportDays: 15,
        shelvingDays: 2,
        estimatedSellableDate: "2026-08-21",
        confidence: "VERIFIED_ANCHOR_ESTIMATED_ETA",
        anchorEvidence: "user_supplied_seaya_inbound_log_screenshot_2026-08-09",
        skuQuantities: {"0021": null}
      }, {
        batchId: "THSL4038-59557",
        totalUnits: 1250,
        createdAt: "2026-08-07T16:41:32+08:00",
        anchorDate: null,
        anchorAt: null,
        anchorType: "REACHED_DOMESTIC_WAREHOUSE_REQUIRED",
        transportDays: 15,
        shelvingDays: 2,
        estimatedSellableDate: null,
        confidence: "BLOCKED_ANCHOR",
        skuQuantities: {"0021": null}
      }]
    },
    VN: {
      totalUnits: 330,
      allocationPolicy: "SINGLE_ACTIVE_BATCH",
      batches: [{
        batchId: "VNML4038-59508",
        totalUnits: 330,
        createdAt: "2026-08-07T13:30:11+08:00",
        anchorDate: null,
        anchorAt: null,
        anchorType: "REACHED_DOMESTIC_WAREHOUSE_REQUIRED",
        transportDays: 15,
        shelvingDays: 2,
        estimatedSellableDate: null,
        confidence: "BLOCKED_ANCHOR"
      }]
    },
    PH: {
      totalUnits: 510,
      allocationPolicy: "SINGLE_ACTIVE_BATCH",
      batches: [{
        batchId: "PHPH4038-59553",
        totalUnits: 510,
        createdAt: "2026-08-07T16:27:07+08:00",
        anchorDate: null,
        anchorAt: null,
        anchorType: "REACHED_DOMESTIC_WAREHOUSE_REQUIRED",
        transportDays: 25,
        shelvingDays: 2,
        estimatedSellableDate: null,
        confidence: "BLOCKED_ANCHOR"
      }]
    }
  }
};
